from __future__ import annotations

import contextlib
import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.orchestration.runs import RunStore
from src.orchestration.state_paths import find_workspace_root, tasks_file_path
from src.tasks import tasks_cli


class TasksCliTest(unittest.TestCase):
    def test_create_prints_json_to_stdout(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = RunStore(root).create_run(initial_prompt="Plan.", workdir=root)
            stdout = io.StringIO()
            stderr = io.StringIO()

            with patch("pathlib.Path.cwd", return_value=root):
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    code = tasks_cli.main(
                        ["--run-id", run.id, "create", "--title", "T1", "--description", "D1", "--client-id", "c1"]
                    )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertTrue(payload["result"]["created"])
            self.assertEqual(payload["result"]["task"]["client_id"], "c1")

    def test_failure_prints_stderr_only(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = RunStore(root).create_run(initial_prompt="Plan.", workdir=root)
            stdout = io.StringIO()
            stderr = io.StringIO()

            with patch("pathlib.Path.cwd", return_value=root):
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    code = tasks_cli.main(["--run-id", run.id, "get", "--id", "missing"])

        self.assertEqual(code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("Unknown task id", stderr.getvalue())

    def test_list_shape_matches_contract(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = RunStore(root).create_run(initial_prompt="Plan.", workdir=root)
            with patch("pathlib.Path.cwd", return_value=root):
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    tasks_cli.main(["--run-id", run.id, "create", "--title", "T1", "--description", "D1"])
            stdout = io.StringIO()
            stderr = io.StringIO()

            with patch("pathlib.Path.cwd", return_value=root):
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    code = tasks_cli.main(["--run-id", run.id, "list", "--view", "all"])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(payload["op"], "list")
            self.assertEqual(payload["result"]["status"], "preparing")
            self.assertEqual(payload["result"]["view"], "all")
            self.assertEqual(payload["result"]["count"], 1)

    def test_ready_with_run_id_updates_run_task_store(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = RunStore(root).create_run(initial_prompt="Plan.", workdir=root)
            stdout = io.StringIO()
            stderr = io.StringIO()

            with patch("pathlib.Path.cwd", return_value=root):
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    code = tasks_cli.main(["--run-id", run.id, "ready"])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(payload["result"]["status"], "ready")

    def test_run_id_is_accepted_after_subcommand(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = RunStore(root).create_run(initial_prompt="Plan.", workdir=root)
            stdout = io.StringIO()
            stderr = io.StringIO()

            with patch("pathlib.Path.cwd", return_value=root):
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    code = tasks_cli.main(["create", "--run-id", run.id, "--title", "T1", "--description", "D1"])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertTrue(payload["result"]["created"])

    def test_worktree_path_resolves_to_parent_workspace(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = RunStore(root).create_run(initial_prompt="Plan.", workdir=root)
            worktree_cwd = root / ".worktrees" / "abc123" / "subdir"
            worktree_cwd.mkdir(parents=True, exist_ok=True)
            expected_path = tasks_file_path(root, run.id)
            stdout = io.StringIO()
            stderr = io.StringIO()

            with patch("pathlib.Path.cwd", return_value=worktree_cwd):
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    code = tasks_cli.main(["create", "--title", "T1", "--description", "D1"])

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertTrue(expected_path.exists())

    def test_default_path_resolves_to_active_run_tasks(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = RunStore(root).create_run(initial_prompt="Plan.", workdir=root)
            expected_path = tasks_file_path(root, run.id)
            stdout = io.StringIO()
            stderr = io.StringIO()

            with patch("pathlib.Path.cwd", return_value=root):
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    code = tasks_cli.main(["create", "--title", "T1", "--description", "D1"])

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertTrue(expected_path.exists())


if __name__ == "__main__":
    unittest.main()
