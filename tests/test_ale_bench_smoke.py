"""End-to-end smoke test for ALE-Bench using the bab orchestration pipeline.

Set BABEL_RUN_ALE_BENCH_SMOKE=1 to run.
"""
from __future__ import annotations

import datetime
import os
import re
import shutil
import subprocess
import time
import unittest
from pathlib import Path

from jinja2 import Template

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
ALE_ENV_DIR = REPO_ROOT / "environments" / "ale_bench_lite"
AGENT_BEST_DIR = ALE_ENV_DIR / "ale_agent_best"
PROMPT_TEMPLATE = SCRIPT_DIR / "prompts" / "ale_bench_smoke_prompt.md"
SERVER_PORT = 8765


def _ensure_server_running() -> None:
    import urllib.request
    import sys

    url = f"http://localhost:{SERVER_PORT}/health"
    try:
        urllib.request.urlopen(url, timeout=2)
        return
    except Exception:
        pass

    subprocess.Popen(
        [sys.executable, str(ALE_ENV_DIR / "server.py")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(15):
        time.sleep(1)
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except Exception:
            pass


@unittest.skipUnless(
    os.environ.get("BABEL_RUN_ALE_BENCH_SMOKE") == "1",
    "set BABEL_RUN_ALE_BENCH_SMOKE=1 to run ALE-Bench smoke tests",
)
class AleBenchSmokeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.problem_id = "ahc008"
        self.initial_score = 183454030

        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = SCRIPT_DIR / "smoke_out" / f"{self.problem_id}_{ts}"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        print(f"\n[smoke] run_dir: {self.run_dir}")

        # Copy baseline solution
        baseline_src = AGENT_BEST_DIR / f"{self.problem_id}.cpp"
        shutil.copy2(baseline_src, self.run_dir / "baseline.cpp")

        # Render prompt template
        template = Template(PROMPT_TEMPLATE.read_text(encoding="utf-8"))
        prompt_text = template.render(
            problem_id=self.problem_id,
            initial_score=self.initial_score,
        )
        (self.run_dir / "prompt.md").write_text(prompt_text, encoding="utf-8")

        # Ensure eval server is running on the host
        _ensure_server_running()

        # Create run_dir/.codex/ and copy auth
        codex_dir = self.run_dir / ".codex"
        codex_dir.mkdir()
        host_auth = Path.home() / ".codex" / "auth.json"
        if host_auth.exists():
            shutil.copy2(host_auth, codex_dir / "auth.json")

        # Start Docker container
        self.container_name = f"ale-bench-smoke-{ts}"
        subprocess.run(
            [
                "docker", "run", "-d",
                "--name", self.container_name,
                "-v", f"{self.run_dir}:/testbed/workspace",
                "-v", f"{self.run_dir / '.codex'}:/root/.codex",
                "-v", f"{REPO_ROOT}:/opt/babel-research",
                "--add-host", "host.docker.internal:host-gateway",
                "ale-bench-lite-worker:latest",
                "sleep", "infinity",
            ],
            check=True,
        )
        subprocess.run(
            ["docker", "exec", self.container_name, "pip", "install", "-q", "-e", "/opt/babel-research"],
            check=True,
        )
        subprocess.run(
            ["docker", "exec", self.container_name,
             "git", "config", "--global", "--add", "safe.directory", "/testbed/workspace"],
            check=True,
        )
        # Init git repo with an initial commit so branches/worktrees work
        (self.run_dir / ".gitignore").write_text(".codex/\n")
        subprocess.run(
            ["docker", "exec", "-w", "/testbed/workspace", self.container_name,
             "git", "init"],
            check=True,
        )
        subprocess.run(
            ["docker", "exec", "-w", "/testbed/workspace", self.container_name,
             "git", "config", "user.email", "smoke@test.local"],
            check=True,
        )
        subprocess.run(
            ["docker", "exec", "-w", "/testbed/workspace", self.container_name,
             "git", "config", "user.name", "Smoke Test"],
            check=True,
        )
        subprocess.run(
            ["docker", "exec", "-w", "/testbed/workspace", self.container_name,
             "git", "add", "."],
            check=True,
        )
        subprocess.run(
            ["docker", "exec", "-w", "/testbed/workspace", self.container_name,
             "git", "commit", "-m", "initial"],
            check=True,
        )

    def tearDown(self) -> None:
        subprocess.run(["docker", "stop", self.container_name], check=False)
        subprocess.run(["docker", "rm", self.container_name], check=False)
        print(f"[smoke] artifacts preserved at: {self.run_dir}")

    def test_smoke_run(self) -> None:
        from src.orchestration.runs import RunStore

        result = subprocess.run(
            ["docker", "exec", "-w", "/testbed/workspace", self.container_name,
             "bab", "run", "/testbed/workspace/prompt.md",
             "--max-rounds", "1", "--max-workers", "2"],
            check=True, capture_output=True, text=True,
        )
        run_id = result.stdout.split()[0]

        store = RunStore(self.run_dir)
        driver_log = self.run_dir / ".babel-agent" / "runs" / run_id / "logs" / "driver.log"
        deadline = time.monotonic() + 15 * 60  # 15 minutes
        while time.monotonic() < deadline:
            time.sleep(10)
            run = store.get_run(run_id)
            if run is None:
                continue
            if run.status == "completed":
                break
            if run.status == "failed":
                log_tail = driver_log.read_text(encoding="utf-8")[-2000:] if driver_log.exists() else "(no driver log)"
                self.fail(f"run {run_id} failed\n\ndriver.log tail:\n{log_tail}")
            # Detect driver crash: status stuck at running but driver process exited with exception
            if driver_log.exists():
                log_text = driver_log.read_text(encoding="utf-8")
                if "driver raised an exception" in log_text and run.status == "running":
                    self.fail(f"run {run_id} driver crashed\n\ndriver.log:\n{log_text[-2000:]}")
        else:
            log_tail = driver_log.read_text(encoding="utf-8")[-2000:] if driver_log.exists() else "(no driver log)"
            self.fail(f"run {run_id} timed out after 15 minutes\n\ndriver.log tail:\n{log_tail}")

        wiki = self.run_dir / "wiki"
        scored_files = [
            p for p in wiki.rglob("*.md")
            if p.name != "MAIN.md" and re.search(r"\d{5,}", p.read_text(encoding="utf-8"))
        ]
        self.assertGreaterEqual(
            len(scored_files), 1,
            f"Expected at least 1 wiki file with a numeric score, found none.\nWiki files: {list(wiki.rglob('*.md'))}",
        )


if __name__ == "__main__":
    unittest.main()
