from __future__ import annotations

import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from src.orchestration.runs import RunStore
from src.orchestration.state_paths import run_file_path, run_index_path, sessions_file_path, tasks_file_path


class RunStoreTest(unittest.TestCase):
    def test_create_and_load_run(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = RunStore(root)
            created = store.create_run(initial_prompt="Plan this.", workdir=root, max_rounds=3, max_workers=5)
            loaded = store.get_run(created.id)

            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.initial_prompt, "Plan this.")
            self.assertEqual(loaded.status, "running")
            self.assertEqual(loaded.current_phase, "planner")
            self.assertEqual(loaded.workdir, root.resolve())
            self.assertEqual(loaded.max_rounds, 3)
            self.assertEqual(loaded.max_workers, 5)
            self.assertEqual(loaded.rounds_completed, 0)
            self.assertEqual(loaded.claimed_task_ids, [])
            self.assertTrue(run_file_path(root, created.id).exists())
            self.assertTrue(sessions_file_path(root, created.id).exists())
            self.assertTrue(tasks_file_path(root, created.id).exists())
            index = __import__("json").loads(run_index_path(root).read_text(encoding="utf-8"))
            self.assertEqual(index["latest_run_id"], created.id)
            self.assertEqual(index["active_run_id"], created.id)

    def test_concurrent_updates_preserve_both_run_changes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_store = RunStore(root)
            second_store = RunStore(root)
            first = first_store.create_run(initial_prompt="Plan A.", workdir=root)
            second = first_store.create_run(initial_prompt="Plan B.", workdir=root)
            first_store._test_write_delay = 0.2

            def update_first() -> None:
                first_store.update_run(replace(first, claimed_task_ids=["task-1"]))

            def update_second() -> None:
                second_store.update_run(replace(second, current_phase="worker", worker_waves=[["worker-1"]]))

            thread_one = threading.Thread(target=update_first)
            thread_two = threading.Thread(target=update_second)
            thread_one.start()
            time.sleep(0.05)
            thread_two.start()
            thread_one.join()
            thread_two.join()

            loaded_first = second_store.get_run(first.id)
            loaded_second = second_store.get_run(second.id)

            self.assertIsNotNone(loaded_first)
            self.assertIsNotNone(loaded_second)
            assert loaded_first is not None
            assert loaded_second is not None
            self.assertEqual(loaded_first.claimed_task_ids, ["task-1"])
            self.assertEqual(loaded_second.current_phase, "worker")
            self.assertEqual(loaded_second.worker_waves, [["worker-1"]])


if __name__ == "__main__":
    unittest.main()
