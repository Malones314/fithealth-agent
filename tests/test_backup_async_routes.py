"""Backup HTTP operations must leave the event loop free to drain requests."""

from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from tests import TEST_DATA_DIR  # noqa: F401 — isolate singleton imports below
from fithealth_agent.backup_service import LocalBackupService
from fithealth_agent.external_model_settings import ExternalModelSettingsStore
from fithealth_agent.health_store import HealthStore
from fithealth_agent.info_store import InfoStore
from fithealth_agent.maintenance import MaintenanceGate
from fithealth_agent.routes import health, maintenance_ops
from fithealth_agent.runtime import middleware


class BackupAsyncRoutesTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.profile = self.root / "user_profile.json"
        self.profile.write_text('{"goal": "maintain"}', encoding="utf-8")
        self.gate = MaintenanceGate()
        self.info_store = InfoStore(self.root / "info_store.json")
        self.health_store = HealthStore(self.root / "health.db", self.root / "health-imports")
        self.settings_store = ExternalModelSettingsStore(self.root / "external_model_settings.json")
        self.service = LocalBackupService(
            self.root,
            gate=self.gate,
            database=self.health_store,
            on_restored=[lambda: {"available": True}],
        )
        self.backup = self.service.export_bytes()
        self.enterContext(patch.object(middleware, "MAINTENANCE", self.gate))
        self.enterContext(patch.object(health, "MAINTENANCE", self.gate))
        self.enterContext(
            patch.object(maintenance_ops.deps, "backup_service", self.service)
        )
        self.enterContext(patch.object(maintenance_ops.deps, "info_store", self.info_store))
        self.enterContext(patch.object(maintenance_ops.deps, "health_store", self.health_store))
        self.enterContext(
            patch.object(maintenance_ops.deps, "external_model_settings_store", self.settings_store)
        )
        # The real restore writes only our temporary directory. Avoid reloading
        # the process-wide pending-workout cache after it completes.
        self.enterContext(
            patch.object(maintenance_ops.workout_store, "reload_from_disk", return_value={})
        )
        self.app = FastAPI()
        self.app.include_router(maintenance_ops.backup_router)
        self.app.include_router(health.storage_router)
        middleware.register(self.app)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        )

    async def test_restore_drains_same_loop_request_and_keeps_diagnostics_reachable(self) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()
        draining = asyncio.Event()
        loop = asyncio.get_running_loop()

        @self.app.get("/inflight")
        async def inflight():
            entered.set()
            await release.wait()
            return {"finished": True}

        def short_drain(timeout: float) -> None:
            loop.call_soon_threadsafe(draining.set)
            MaintenanceGate._drain(self.gate, timeout=0.2)

        self.enterContext(patch.object(self.gate, "_drain", side_effect=short_drain))
        self.profile.write_text('{"goal": "changed"}', encoding="utf-8")

        async with self._client() as client:
            pending_request = asyncio.create_task(client.get("/inflight"))
            await asyncio.wait_for(entered.wait(), timeout=5)
            restoring = asyncio.create_task(
                client.post(
                    "/data/backup/import",
                    data={"confirm_restore": "true"},
                    files={"file": ("backup.zip", self.backup, "application/zip")},
                )
            )
            try:
                await asyncio.wait_for(draining.wait(), timeout=5)
                diagnostic = await client.get("/health/storage-status")
            finally:
                release.set()
                request_response, restore_response = await asyncio.wait_for(
                    asyncio.gather(pending_request, restoring), timeout=5
                )

        self.assertEqual(request_response.status_code, 200)
        self.assertEqual(restore_response.status_code, 200, restore_response.text)
        self.assertTrue(restore_response.json()["restored"])
        self.assertEqual(diagnostic.status_code, 200)
        self.assertTrue(diagnostic.json()["maintenance"]["active"])
        self.assertEqual(json.loads(self.profile.read_text(encoding="utf-8"))["goal"], "maintain")
        self.assertFalse(self.gate.active)
        self.assertEqual(self.gate.inflight, 0)

    async def test_real_diagnostics_do_not_wait_for_restore_file_locks(self) -> None:
        applying = asyncio.Event()
        release = threading.Event()
        apply_finished = threading.Event()
        loop = asyncio.get_running_loop()
        apply = self.service._apply

        def held_apply(files, *, version):
            # LocalBackupService already holds the real JSON and database
            # locks here. Diagnostic reads must not wait for those locks.
            loop.call_soon_threadsafe(applying.set)
            try:
                release.wait(timeout=1)
                return apply(files, version=version)
            finally:
                apply_finished.set()

        self.enterContext(patch.object(self.service, "_apply", side_effect=held_apply))
        async with self._client() as client:
            restoring = asyncio.create_task(
                client.post(
                    "/data/backup/import",
                    data={"confirm_restore": "true"},
                    files={"file": ("backup.zip", self.backup, "application/zip")},
                )
            )
            try:
                await asyncio.wait_for(applying.wait(), timeout=5)
                diagnostic = await client.get("/health/storage-status")
                answered_during_restore = not apply_finished.is_set()
            finally:
                release.set()
                response = await asyncio.wait_for(restoring, timeout=5)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(diagnostic.status_code, 200)
        self.assertTrue(answered_during_restore, "Diagnostics waited for restore's file locks")
        self.assertTrue(diagnostic.json()["maintenance"]["active"])
        self.assertTrue(diagnostic.json()["check_deferred"])

    async def test_inspection_keeps_diagnostics_reachable_before_validation_finishes(self) -> None:
        started = asyncio.Event()
        release = threading.Event()
        finished = threading.Event()
        loop = asyncio.get_running_loop()
        validate = self.service.validate

        def slow_validate(content: bytes):
            loop.call_soon_threadsafe(started.set)
            try:
                # A bounded wait makes the unfixed route fail without hanging
                # the suite. With a responsive loop, diagnostics release it.
                release.wait(timeout=0.5)
                return validate(content)
            finally:
                finished.set()

        self.enterContext(patch.object(self.service, "validate", side_effect=slow_validate))
        async with self._client() as client:
            inspection = asyncio.create_task(
                client.post(
                    "/data/backup/inspect",
                    files={"file": ("backup.zip", self.backup, "application/zip")},
                )
            )
            try:
                await asyncio.wait_for(started.wait(), timeout=5)
                diagnostic = await client.get("/health/storage-status")
                validation_was_pending = not finished.is_set()
            finally:
                release.set()
                response = await asyncio.wait_for(inspection, timeout=5)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["valid"])
        self.assertEqual(diagnostic.status_code, 200)
        self.assertTrue(
            validation_was_pending,
            "The diagnostic request could not finish until synchronous validation returned",
        )

    async def test_pending_state_is_reloaded_before_maintenance_ends(self) -> None:
        states = []
        def reload_state():
            states.append(self.gate.active)
            return {"has_workout": False}
        self.service._on_restored = (
            lambda: {"available": True},
            lambda: 0,
            maintenance_ops.deps._reload_workout_after_restore,
        )
        with patch.object(maintenance_ops.workout_store, "reload_from_disk", side_effect=reload_state):
            async with self._client() as client:
                response = await client.post(
                    "/data/backup/import", data={"confirm_restore": "true"},
                    files={"file": ("backup.zip", self.backup, "application/zip")},
                )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["workout_state"], {"has_workout": False})
        self.assertEqual(states, [True])


if __name__ == "__main__":
    unittest.main()
