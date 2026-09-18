from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from fithealth_agent.maintenance import MaintenanceGate
from fithealth_agent.runtime import frontend
from fithealth_agent.runtime.middleware import maintenance_guard


class FrontendDeliveryTest(unittest.TestCase):
    def test_built_frontend_and_hashed_asset_are_served(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "assets").mkdir()
            (root / "index.html").write_text("<script src='/assets/app-test.js'></script>")
            (root / "assets" / "app-test.js").write_text("console.log('ok')")
            with patch.object(frontend, "DIST_DIR", root):
                app = FastAPI()
                frontend.register(app)
                client = TestClient(app)
                self.assertEqual(client.get("/").status_code, 200)
                self.assertEqual(client.get("/assets/app-test.js").status_code, 200)

    def test_missing_build_returns_actionable_503(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(frontend, "DIST_DIR", Path(directory)):
                app = FastAPI()
                frontend.register(app)
                response = TestClient(app).get("/")
        self.assertEqual(response.status_code, 503)
        self.assertIn("npm ci && npm run build", response.text)

    def test_static_frontend_paths_remain_available_during_maintenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "assets").mkdir()
            (root / "index.html").write_text("<link href='/assets/app.css' rel='stylesheet'>")
            (root / "assets" / "app.css").write_text("body {}")
            with patch.object(frontend, "DIST_DIR", root):
                app = FastAPI()
                app.middleware("http")(maintenance_guard)
                frontend.register(app)
                import fithealth_agent.runtime.middleware as middleware

                gate = MaintenanceGate()
                with patch.object(middleware, "MAINTENANCE", gate):
                    with gate.exclusive("test", timeout=1):
                        response = TestClient(app).get("/assets/app.css")
                self.assertEqual(response.status_code, 200)

    def test_removed_legacy_path_is_not_mounted(self) -> None:
        app = FastAPI()
        frontend.register(app)
        self.assertEqual(TestClient(app).get("/legacy/index.html").status_code, 404)


if __name__ == "__main__":
    unittest.main()
