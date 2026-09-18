from __future__ import annotations

import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fithealth_agent.info_store import InfoStore
from fithealth_agent.plan_store import TrainingPlanStore
from fithealth_agent.storage import DailyRecordStore


class JsonStoreLockingTest(unittest.TestCase):
    def _run_concurrently(self, actions) -> list[BaseException]:
        barrier = threading.Barrier(len(actions))
        errors: list[BaseException] = []

        def run(action) -> None:
            try:
                barrier.wait(timeout=5)
                action()
            except BaseException as exc:  # noqa: BLE001 - 汇总到主线程断言
                errors.append(exc)

        workers = [threading.Thread(target=run, args=(action,)) for action in actions]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)
        self.assertTrue(all(not worker.is_alive() for worker in workers), "并发读写发生死锁")
        return errors

    def test_store_instances_preserve_concurrent_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            daily = [DailyRecordStore(root / "daily.json") for _ in range(6)]
            errors = self._run_concurrently([
                lambda index=index: daily[index].add_record(
                    "2026-08-18", "training", {"name": str(index)}
                )
                for index in range(6)
            ])
            self.assertEqual(errors, [])
            self.assertEqual(len(daily[0].list_records()), 6)

            plans = [TrainingPlanStore(root / "plans.json") for _ in range(6)]
            errors = self._run_concurrently([
                lambda index=index: plans[index].add(
                    date="2026-08-18", subject="胸部", title=str(index),
                    content=str(index), source="agent_generated",
                )
                for index in range(6)
            ])
            self.assertEqual(errors, [])
            self.assertEqual(len(plans[0].list_plans()), 6)

            memories = [InfoStore(root / "memories.json") for _ in range(6)]
            expiry = datetime.now(timezone.utc) + timedelta(days=1)
            errors = self._run_concurrently([
                lambda index=index: memories[index].add_entry(str(index), {}, expiry)
                for index in range(6)
            ])
            self.assertEqual(errors, [])
            self.assertEqual(len(memories[0].get_all()), 6)


if __name__ == "__main__":
    unittest.main()
