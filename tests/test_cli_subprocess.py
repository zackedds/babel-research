from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


def _uv() -> str:
    path = __import__("shutil").which("uv")
    if path is not None:
        return path
    raise unittest.SkipTest("No Python 3.11+ interpreter is available for CLI subprocess tests")


def _install_cli_environment(root: Path) -> tuple[Path, list[str], list[str], dict[str, str]]:
    repo_root = Path.cwd()
    uv = _uv()
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    env["UV_PROJECT_ENVIRONMENT"] = str(root / ".venv")
    subprocess.run([uv, "sync"], check=True, cwd=repo_root, env=env)
    return repo_root, [uv, "run", "bab"], [uv, "run", "tasks"], env


class CliSubprocessTest(unittest.TestCase):
    def test_plain_unittest_discovers_repository_suite(self) -> None:
        repo_root = Path.cwd()
        uv = _uv()
        env = os.environ.copy()
        env.pop("VIRTUAL_ENV", None)

        result = subprocess.run(
            [
                uv,
                "run",
                "python",
                "-c",
                (
                    "import unittest; "
                    "suite = unittest.defaultTestLoader.discover('tests'); "
                    "count = suite.countTestCases(); "
                    "print(count); "
                    "raise SystemExit(0 if count > 0 else 1)"
                ),
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            env=env,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertGreater(int(result.stdout.strip()), 0)

    def test_bab_entrypoint_reports_missing_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo_root, bab_cmd, _, env = _install_cli_environment(root)
            result = subprocess.run(
                [*bab_cmd, str(root / "missing.md")],
                cwd=repo_root,
                capture_output=True,
                text=True,
                env=env,
            )

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("missing.md", result.stderr)

    def test_src_cli_import_remains_available(self) -> None:
        repo_root = Path.cwd()
        uv = _uv()
        env = os.environ.copy()
        env.pop("VIRTUAL_ENV", None)

        result = subprocess.run(
            [
                uv,
                "run",
                "python",
                "-c",
                "from src.bab_cli.cli import main; print(callable(main))",
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            env=env,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "True")

    def test_tasks_entrypoint_supports_success_and_failure(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks_path = root / "tasks.json"
            repo_root, _, tasks_cmd, env = _install_cli_environment(root)

            create_result = subprocess.run(
                [
                    *tasks_cmd,
                    "--tasks-path",
                    str(tasks_path),
                    "create",
                    "--title",
                    "T1",
                    "--description",
                    "D1",
                ],
                cwd=repo_root,
                capture_output=True,
                text=True,
                env=env,
            )
            get_result = subprocess.run(
                [
                    *tasks_cmd,
                    "--tasks-path",
                    str(tasks_path),
                    "get",
                    "--id",
                    "task-999",
                ],
                cwd=repo_root,
                capture_output=True,
                text=True,
                env=env,
            )

        self.assertEqual(create_result.returncode, 0)
        self.assertIn('"op": "create"', create_result.stdout)
        self.assertEqual(create_result.stderr, "")
        self.assertEqual(get_result.returncode, 1)
        self.assertEqual(get_result.stdout, "")
        self.assertIn("Unknown task id", get_result.stderr)


if __name__ == "__main__":
    unittest.main()
