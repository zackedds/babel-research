from __future__ import annotations

import contextlib
import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.bab_cli import cli
from src.utils.models import OrchestrationRun


class FakeRunStore:
    last_root: Path | None = None
    next_error: Exception | None = None
    last_prompt: str | None = None
    last_workdir: Path | None = None
    last_max_rounds: int | None = None
    last_max_workers: int | None = None

    def __init__(self, root: Path) -> None:
        type(self).last_root = root
        if type(self).next_error is not None:
            raise type(self).next_error

    def create_run(
        self,
        *,
        initial_prompt: str,
        workdir: Path,
        max_rounds: int | None = None,
        max_workers: int | None = None,
    ) -> OrchestrationRun:
        type(self).last_prompt = initial_prompt
        type(self).last_workdir = workdir
        type(self).last_max_rounds = max_rounds
        type(self).last_max_workers = max_workers
        return OrchestrationRun(
            id="deadbeef",
            status="running",
            initial_prompt=initial_prompt,
            workdir=workdir,
            max_rounds=max_rounds,
            max_workers=max_workers,
            rounds_completed=0,
            planner_session_ids=[],
            worker_waves=[],
            claimed_task_ids=[],
            current_phase="planner",
            created_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
            updated_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        )


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        FakeRunStore.last_root = None
        FakeRunStore.next_error = None
        FakeRunStore.last_prompt = None
        FakeRunStore.last_workdir = None
        FakeRunStore.last_max_rounds = None
        FakeRunStore.last_max_workers = None

    def test_run_file_reads_prompt_and_starts_background_run(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompt_path = root / "program.md"
            prompt_path.write_text("Plan this system.", encoding="utf-8")

            with patch("src.bab_cli.cli.RunStore", FakeRunStore):
                with patch("src.bab_cli.cli.spawn_orchestration_driver") as spawn_driver:
                    result = cli.run_file(prompt_path, root, root=root)

            self.assertEqual(result, {"ok": True, "run_id": "deadbeef"})
            self.assertEqual(FakeRunStore.last_prompt, "Plan this system.")
            self.assertEqual(FakeRunStore.last_workdir, root.resolve())
            self.assertIsNone(FakeRunStore.last_max_rounds)
            self.assertIsNone(FakeRunStore.last_max_workers)
            spawn_driver.assert_called_once_with(root, "deadbeef", state_root=root.resolve())

    def test_run_file_passes_max_rounds_and_max_workers(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompt_path = root / "program.md"
            prompt_path.write_text("Plan this system.", encoding="utf-8")

            with patch("src.bab_cli.cli.RunStore", FakeRunStore):
                with patch("src.bab_cli.cli.spawn_orchestration_driver"):
                    result = cli.run_file(prompt_path, root, root=root, max_rounds=2, max_workers=4)

            self.assertEqual(result, {"ok": True, "run_id": "deadbeef"})
            self.assertEqual(FakeRunStore.last_max_rounds, 2)
            self.assertEqual(FakeRunStore.last_max_workers, 4)

    def test_main_prints_run_status_lines(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompt_path = root / "program.md"
            prompt_path.write_text("Prompt body", encoding="utf-8")

            stdout = io.StringIO()
            stderr = io.StringIO()
            with patch("src.bab_cli.cli.RunStore", FakeRunStore):
                with patch("src.bab_cli.cli.spawn_orchestration_driver"):
                    with patch("pathlib.Path.cwd", return_value=root):
                        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                            code = cli.main([str(prompt_path)])

            self.assertEqual(code, 0)
            self.assertEqual(
                stdout.getvalue(),
                "deadbeef run started\nMonitor run with: bab activity deadbeef\n",
            )
            self.assertEqual(stderr.getvalue(), "")

    def test_main_supports_explicit_run_subcommand(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompt_path = root / "program.md"
            prompt_path.write_text("Prompt body", encoding="utf-8")

            stdout = io.StringIO()
            stderr = io.StringIO()
            with patch("src.bab_cli.cli.RunStore", FakeRunStore):
                with patch("src.bab_cli.cli.spawn_orchestration_driver"):
                    with patch("pathlib.Path.cwd", return_value=root):
                        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                            code = cli.main(["run", str(prompt_path)])

            self.assertEqual(code, 0)
            self.assertEqual(
                stdout.getvalue(),
                "deadbeef run started\nMonitor run with: bab activity deadbeef\n",
            )
            self.assertEqual(stderr.getvalue(), "")

    def test_main_supports_run_flags(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompt_path = root / "program.md"
            prompt_path.write_text("Prompt body", encoding="utf-8")

            stdout = io.StringIO()
            stderr = io.StringIO()
            with patch("src.bab_cli.cli.RunStore", FakeRunStore):
                with patch("src.bab_cli.cli.spawn_orchestration_driver"):
                    with patch("pathlib.Path.cwd", return_value=root):
                        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                            code = cli.main(["--max-rounds", "2", "--max-workers", "3", str(prompt_path)])

            self.assertEqual(code, 0)
            self.assertEqual(
                stdout.getvalue(),
                "deadbeef run started\nMonitor run with: bab activity deadbeef\n",
            )
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(FakeRunStore.last_max_rounds, 2)
            self.assertEqual(FakeRunStore.last_max_workers, 3)

    def test_main_inspect_prints_snapshot(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch("src.bab_cli.cli.inspect_run", return_value={"run": {"id": "deadbeef"}}):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = cli.main(["inspect", "--run-id", "deadbeef"])

        self.assertEqual(code, 0)
        self.assertEqual(__import__("json").loads(stdout.getvalue()), {"ok": True, "op": "inspect", "result": {"run": {"id": "deadbeef"}}})
        self.assertEqual(stderr.getvalue(), "")

    def test_main_activity_snapshot_prints_json(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch("src.bab_cli.cli.activity_snapshot", return_value={"run": {"id": "deadbeef"}}):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = cli.main(["activity", "--snapshot", "--run-id", "deadbeef"])

        self.assertEqual(code, 0)
        self.assertEqual(
            __import__("json").loads(stdout.getvalue()),
            {"ok": True, "op": "activity_snapshot", "result": {"run": {"id": "deadbeef"}}},
        )
        self.assertEqual(stderr.getvalue(), "")

    def test_run_activity_runs_tui_process(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            entrypoint = root / "src" / "tui" / "activity-feed" / "src" / "index.tsx"
            entrypoint.parent.mkdir(parents=True)
            entrypoint.write_text("", encoding="utf-8")
            completed = __import__("subprocess").CompletedProcess(args=["bun"], returncode=0)
            with patch("src.bab_cli.cli.shutil.which", return_value="/usr/local/bin/bun"):
                with patch("src.bab_cli.cli.subprocess.run", return_value=completed) as run:
                    code = cli.run_activity(root, root=root, run_id="deadbeef")

        self.assertEqual(code, 0)
        command = run.call_args.kwargs["args"] if "args" in run.call_args.kwargs else run.call_args.args[0]
        self.assertEqual(command[0], "/usr/local/bin/bun")
        self.assertIn("index.tsx", command[2])
        self.assertEqual(command[-2:], ["--python", cli.sys.executable])

    def test_main_returns_error_for_missing_file(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(["missing.md"])

        self.assertEqual(code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("missing.md", stderr.getvalue())

    def test_main_returns_error_for_orchestrator_failure(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompt_path = root / "program.md"
            prompt_path.write_text("Prompt body", encoding="utf-8")
            FakeRunStore.next_error = RuntimeError("run store failed")

            stdout = io.StringIO()
            stderr = io.StringIO()
            with patch("src.bab_cli.cli.RunStore", FakeRunStore):
                with patch("pathlib.Path.cwd", return_value=root):
                    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                        code = cli.main([str(prompt_path)])

            self.assertEqual(code, 1)
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn("run store failed", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
