from __future__ import annotations

import json
import multiprocessing
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from src.tasks.store import _tasks_load, tasks


def _concurrent_create(path_str: str, title: str) -> str:
    result = tasks("create", path=Path(path_str), title=title, description="D", _test_delay=0.1)
    return result["result"]["task"]["id"]


class TasksStoreTest(unittest.TestCase):
    def test_missing_file_bootstraps_empty_store(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            self.assertEqual(_tasks_load(path), {"status": "preparing", "next_id": 1, "tasks": {}, "client_ids": {}})

    def test_corrupt_json_bootstraps_empty_store(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            path.write_text("{not-json", encoding="utf-8")
            self.assertEqual(_tasks_load(path), {"status": "preparing", "next_id": 1, "tasks": {}, "client_ids": {}})

    def test_ready_and_prepare_update_global_status(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"

            ready = tasks("ready", path=path)
            prepared = tasks("prepare", path=path)
            listed = tasks("list", path=path, view="all")

            self.assertEqual(ready["result"]["status"], "ready")
            self.assertEqual(prepared["result"]["status"], "preparing")
            self.assertEqual(listed["result"]["status"], "preparing")

    def test_create_and_client_id_deduplicate(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            created = tasks("create", path=path, title="T1", description="D1", client_id="c1")
            duplicate = tasks("create", path=path, title="T2", description="D2", client_id="c1")

            self.assertTrue(created["result"]["created"])
            self.assertFalse(duplicate["result"]["created"])
            self.assertEqual(created["result"]["task"]["id"], duplicate["result"]["task"]["id"])

    def test_get_unknown_task_fails(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            with self.assertRaisesRegex(ValueError, "Unknown task id"):
                tasks("get", path=path, id="task-999")

    def test_update_requires_open_task_and_fields(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            created = tasks("create", path=path, title="T1", description="D1")
            task_id = created["result"]["task"]["id"]

            with self.assertRaisesRegex(ValueError, "title and/or description"):
                tasks("update", path=path, id=task_id)

            tasks("close", path=path, id=task_id, reason="done")
            with self.assertRaisesRegex(ValueError, "status open or assigned"):
                tasks("update", path=path, id=task_id, title="T2")

    def test_ready_state_respects_open_blockers(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            blocker = tasks("create", path=path, title="Blocker", description="D1")
            blocked = tasks("create", path=path, title="Blocked", description="D2")
            blocker_id = blocker["result"]["task"]["id"]
            blocked_id = blocked["result"]["task"]["id"]

            tasks("dep_add", path=path, blocker_id=blocker_id, blocked_id=blocked_id)
            ready_before = tasks("get", path=path, id=blocked_id)
            tasks("close", path=path, id=blocker_id, reason="done")
            ready_after = tasks("get", path=path, id=blocked_id)

            self.assertFalse(ready_before["result"]["ready"])
            self.assertTrue(ready_after["result"]["ready"])

    def test_note_append_adds_timestamped_note(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            created = tasks("create", path=path, title="T1", description="D1")
            task_id = created["result"]["task"]["id"]

            updated = tasks("note_append", path=path, id=task_id, note="hello")

            self.assertEqual(updated["result"]["notes"][0]["text"], "hello")
            self.assertIn("ts", updated["result"]["notes"][0])

    def test_assign_marks_task_unready_without_closing_it(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            created = tasks("create", path=path, title="T1", description="D1")
            task_id = created["result"]["task"]["id"]

            assigned = tasks("assign", path=path, id=task_id)
            fetched = tasks("get", path=path, id=task_id)
            open_view = tasks("list", path=path, view="open")
            assigned_view = tasks("list", path=path, view="assigned")
            ready_view = tasks("list", path=path, view="ready")

            self.assertTrue(assigned["result"]["changed"])
            self.assertEqual(fetched["result"]["status"], "assigned")
            self.assertFalse(fetched["result"]["ready"])
            self.assertEqual([task["id"] for task in open_view["result"]["tasks"]], [task_id])
            self.assertEqual([task["id"] for task in assigned_view["result"]["tasks"]], [task_id])
            self.assertEqual(ready_view["result"]["tasks"], [])

    def test_assign_requires_ready_open_task(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            blocker = tasks("create", path=path, title="Blocker", description="D1")
            blocked = tasks("create", path=path, title="Blocked", description="D2")
            blocker_id = blocker["result"]["task"]["id"]
            blocked_id = blocked["result"]["task"]["id"]
            tasks("dep_add", path=path, blocker_id=blocker_id, blocked_id=blocked_id)

            with self.assertRaisesRegex(ValueError, "task ready"):
                tasks("assign", path=path, id=blocked_id)

            tasks("close", path=path, id=blocker_id, reason="done")
            first = tasks("assign", path=path, id=blocked_id)
            second = tasks("assign", path=path, id=blocked_id)

            self.assertTrue(first["result"]["changed"])
            self.assertFalse(second["result"]["changed"])

    def test_dependency_linking_is_bidirectional_and_idempotent(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            blocker = tasks("create", path=path, title="Blocker", description="D1")
            blocked = tasks("create", path=path, title="Blocked", description="D2")
            blocker_id = blocker["result"]["task"]["id"]
            blocked_id = blocked["result"]["task"]["id"]

            first = tasks("dep_add", path=path, blocker_id=blocker_id, blocked_id=blocked_id)
            second = tasks("dep_add", path=path, blocker_id=blocker_id, blocked_id=blocked_id)
            blocker_task = tasks("get", path=path, id=blocker_id)
            blocked_task = tasks("get", path=path, id=blocked_id)

            self.assertTrue(first["result"]["changed"])
            self.assertFalse(second["result"]["changed"])
            self.assertEqual(blocker_task["result"]["blocked"], [blocked_id])
            self.assertEqual(blocked_task["result"]["blockers"], [blocker_id])

    def test_close_updates_status_and_reason(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            created = tasks("create", path=path, title="T1", description="D1")
            task_id = created["result"]["task"]["id"]

            closed = tasks("close", path=path, id=task_id, reason="done")

            self.assertEqual(closed["result"]["status"], "closed")
            self.assertEqual(closed["result"]["close_reason"], "done")

    def test_close_accepts_assigned_task(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            created = tasks("create", path=path, title="T1", description="D1")
            task_id = created["result"]["task"]["id"]
            tasks("assign", path=path, id=task_id)

            closed = tasks("close", path=path, id=task_id, reason="done")

            self.assertEqual(closed["result"]["status"], "closed")

    def test_soft_delete_marks_deleted(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            created = tasks("create", path=path, title="T1", description="D1")
            task_id = created["result"]["task"]["id"]

            deleted = tasks("delete", path=path, id=task_id)

            self.assertEqual(deleted["result"]["status"], "deleted")

    def test_hard_delete_removes_reverse_refs_and_client_mapping(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            blocker = tasks("create", path=path, title="Blocker", description="D1", client_id="c1")
            blocked = tasks("create", path=path, title="Blocked", description="D2", client_id="c2")
            blocker_id = blocker["result"]["task"]["id"]
            blocked_id = blocked["result"]["task"]["id"]
            tasks("dep_add", path=path, blocker_id=blocker_id, blocked_id=blocked_id)

            deleted = tasks("delete", path=path, id=blocker_id, hard=True)
            remaining = tasks("get", path=path, id=blocked_id)
            data = json.loads(path.read_text(encoding="utf-8"))

            self.assertTrue(deleted["result"]["deleted"])
            self.assertEqual(remaining["result"]["blockers"], [])
            self.assertNotIn("c1", data["client_ids"])

    def test_list_view_filtering_pagination_and_include_full(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            first = tasks("create", path=path, title="T1", description="D1")
            second = tasks("create", path=path, title="T2", description="D2")
            first_id = first["result"]["task"]["id"]
            second_id = second["result"]["task"]["id"]
            tasks("close", path=path, id=first_id, reason="done")

            open_view = tasks("list", path=path, view="open")
            closed_view = tasks("list", path=path, view="closed")
            all_view = tasks("list", path=path, view="all", include_full=True, limit=1, offset=1)

            self.assertEqual([task["id"] for task in open_view["result"]["tasks"]], [second_id])
            self.assertEqual([task["id"] for task in closed_view["result"]["tasks"]], [first_id])
            self.assertEqual(open_view["result"]["status"], "preparing")
            self.assertEqual(all_view["result"]["total"], 2)
            self.assertEqual(all_view["result"]["count"], 1)
            self.assertIn("description", all_view["result"]["tasks"][0])

    def test_parallel_creates_do_not_clobber_store(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"

            with multiprocessing.Pool(processes=4) as pool:
                task_ids = pool.starmap(
                    _concurrent_create,
                    [(str(path), f"T{i}") for i in range(4)],
                )

            store = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(sorted(task_ids), ["task-1", "task-2", "task-3", "task-4"])
            self.assertEqual(sorted(store["tasks"]), ["task-1", "task-2", "task-3", "task-4"])
            self.assertEqual(store["next_id"], 5)


    def test_create_stores_branch_field(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            result = tasks("create", path=path, title="T1", description="D1", branch="feature/x")
            task = result["result"]["task"]
            self.assertEqual(task["branch"], "feature/x")

    def test_create_branch_defaults_to_empty_string(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            result = tasks("create", path=path, title="T1", description="D1")
            self.assertEqual(result["result"]["task"]["branch"], "")

    def test_full_task_includes_branch(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            tasks("create", path=path, title="T1", description="D1", branch="feature/y")
            task_id = tasks("list", path=path, view="all", include_full=True)["result"]["tasks"][0]["id"]
            got = tasks("get", path=path, id=task_id)["result"]
            self.assertEqual(got["branch"], "feature/y")


if __name__ == "__main__":
    unittest.main()
