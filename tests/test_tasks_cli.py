from __future__ import annotations

import contextlib
import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.orchestration.runs import RunStore
from src.orchestration.state_paths import tasks_file_path
from src.tasks import tasks_cli


class TasksCliTest(unittest.TestCase):
    def test_create_prints_json_to_stdout(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            stdout = io.StringIO()
            stderr = io.StringIO()

            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = tasks_cli.main(
                    ["--tasks-path", str(path), "create", "--title", "T1", "--description", "D1", "--client-id", "c1"]
                )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertTrue(payload["result"]["created"])
            self.assertEqual(payload["result"]["task"]["client_id"], "c1")

    def test_failure_prints_stderr_only(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = tasks_cli.main(["get", "--id", "missing"])

        self.assertEqual(code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("Unknown task id", stderr.getvalue())

    def test_list_shape_matches_contract(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                tasks_cli.main(["--tasks-path", str(path), "create", "--title", "T1", "--description", "D1"])
            stdout = io.StringIO()
            stderr = io.StringIO()

            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = tasks_cli.main(["--tasks-path", str(path), "list", "--view", "all"])

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

    def test_tasks_path_overrides_default_path(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            custom_path = root / "custom" / "tasks.json"
            stdout = io.StringIO()
            stderr = io.StringIO()

            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = tasks_cli.main(["--tasks-path", str(custom_path), "create", "--title", "T1", "--description", "D1"])

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertTrue(custom_path.exists())
            self.assertFalse((root / ".babel-agent" / "tasks.json").exists())

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
