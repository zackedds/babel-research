"""Concurrency stress test for the ALE-Bench evaluator server.

Spins up a single Docker container and fires graduated waves of concurrent
ale-bench-eval invocations to validate parallel session-pool handling.

Tests run in order from minimal (1 worker, 1 wave) up to full load
(10 workers, 10 waves) so the first failure pinpoints the concurrency level
that breaks.

Set BABEL_RUN_ALE_BENCH_CONCURRENCY=1 to run.
"""
from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
ALE_ENV_DIR = REPO_ROOT / "environments" / "ale_bench_lite"
SERVER_PORT = 8765

PROBLEMS = [
    "ahc008",
    "ahc011",
    "ahc015",
    "ahc016",
    "ahc024",
    "ahc025",
    "ahc026",
    "ahc027",
    "ahc039",
    "ahc046",
]


# Inner worker script template executed inside the container.
# Parameters: workers, waves, problems_per_worker (a Python list literal).
_EVAL_WORKER_SCRIPT = """\
\"\"\"Inner worker: {workers} threads x {waves} waves, barrier-synchronised per wave.\"\"\"
import json
import subprocess
import sys
import threading
import time

WORKERS = {workers}
WAVES = {waves}
PROBLEMS_PER_WORKER = {problems_per_worker!r}
ALE_AGENT_BEST = "/opt/babel-research/environments/ale_bench_lite/ale_agent_best"

results = []
results_lock = threading.Lock()
barrier = threading.Barrier(WORKERS)


def worker(worker_id):
    problem_id = PROBLEMS_PER_WORKER[worker_id]
    code_path = f"{{ALE_AGENT_BEST}}/{{problem_id}}.cpp"
    proc = None
    for wave in range(WAVES):
        barrier.wait()
        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                ["ale-bench-eval", "--problem-id", problem_id, "--code-path", code_path],
                capture_output=True,
                text=True,
            )
            elapsed = time.monotonic() - t0
            success = proc.returncode == 0
            judge_result = None
            absolute_score = None
            error = None
            if success:
                try:
                    data = json.loads(proc.stdout)
                    judge_result = data.get("overall_judge_result")
                    absolute_score = data.get("overall_absolute_score")
                except Exception as e:
                    success = False
                    error = f"json parse error: {{e}}; stdout={{proc.stdout!r}}"
            else:
                error = proc.stderr or proc.stdout
        except Exception as exc:
            elapsed = time.monotonic() - t0
            success = False
            judge_result = None
            absolute_score = None
            error = str(exc)

        record = {{
            "worker_id": worker_id,
            "problem_id": problem_id,
            "wave": wave,
            "success": success,
            "return_code": proc.returncode if proc is not None else -1,
            "judge_result": judge_result,
            "absolute_score": absolute_score,
            "elapsed_s": round(elapsed, 3),
            "error": error,
        }}
        with results_lock:
            results.append(record)


threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(WORKERS)]
for t in threads:
    t.start()
for t in threads:
    t.join()

print(json.dumps(results))
if any(not r["success"] for r in results):
    sys.exit(1)
"""


def _ensure_server_running() -> None:
    import urllib.request

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
    os.environ.get("BABEL_RUN_ALE_BENCH_CONCURRENCY") == "1",
    "set BABEL_RUN_ALE_BENCH_CONCURRENCY=1 to run ALE-Bench concurrency tests",
)
class AleBenchEvaluatorConcurrentTest(unittest.TestCase):
    """Graduated concurrency tests sharing a single Docker container."""

    container_name: str = ""
    run_dir: Path = None  # type: ignore[assignment]

    @classmethod
    def setUpClass(cls) -> None:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        cls.run_dir = SCRIPT_DIR / "concurrency_out" / f"concurrent_{ts}"
        cls.run_dir.mkdir(parents=True, exist_ok=True)
        print(f"\n[concurrent] run_dir: {cls.run_dir}")

        _ensure_server_running()

        cls.container_name = f"ale-bench-concurrent-{ts}"
        subprocess.run(
            [
                "docker", "run", "-d",
                "--name", cls.container_name,
                "-v", f"{cls.run_dir}:/testbed/workspace",
                "-v", f"{REPO_ROOT}:/opt/babel-research",
                "--add-host", "host.docker.internal:host-gateway",
                "ale-bench-lite-worker:latest",
                "sleep", "infinity",
            ],
            check=True,
        )
        subprocess.run(
            ["docker", "exec", cls.container_name, "pip", "install", "-q", "-e", "/opt/babel-research"],
            check=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        subprocess.run(["docker", "stop", cls.container_name], check=False)
        subprocess.run(["docker", "rm", cls.container_name], check=False)
        print(f"\n[concurrent] artifacts preserved at: {cls.run_dir}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _run_concurrent_eval(
        self,
        workers: int,
        waves: int,
        problems_per_worker: list[str],
        label: str,
    ) -> list[dict]:
        print(f"\n[concurrent] {label}: {workers}w x {waves} waves = {workers * waves} invocations")

        script = _EVAL_WORKER_SCRIPT.format(
            workers=workers,
            waves=waves,
            problems_per_worker=problems_per_worker,
        )
        script_name = f"eval_worker_{label}.py"
        (self.run_dir / script_name).write_text(script, encoding="utf-8")

        result = subprocess.run(
            [
                "docker", "exec", "-w", "/testbed/workspace", self.container_name,
                "python", script_name,
            ],
            capture_output=True,
            text=True,
            timeout=30 * 60,
        )

        out_dir = self.run_dir / label
        out_dir.mkdir(exist_ok=True)
        (out_dir / "stdout.txt").write_text(result.stdout, encoding="utf-8")
        (out_dir / "stderr.txt").write_text(result.stderr, encoding="utf-8")

        if result.returncode != 0 and not result.stdout.strip():
            self.fail(
                f"[{label}] exited {result.returncode} with no JSON output.\n"
                f"stderr:\n{result.stderr[-3000:]}"
            )

        try:
            records = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            self.fail(
                f"[{label}] could not parse JSON: {e}\nstdout:\n{result.stdout[:2000]}"
            )

        (out_dir / "results.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
        return records

    def _assert_records(
        self,
        records: list[dict],
        workers: int,
        waves: int,
        label: str,
    ) -> None:
        self.assertEqual(
            len(records), workers * waves,
            f"[{label}] expected {workers * waves} records, got {len(records)}",
        )

        failures = [r for r in records if not r["success"] or r["return_code"] != 0]
        if failures:
            self.fail(
                f"[{label}] {len(failures)} failed invocations:\n"
                f"{json.dumps(failures[:5], indent=2)}"
            )

        non_accepted = [r for r in records if r["judge_result"] != "JudgeResult.ACCEPTED"]
        if non_accepted:
            self.fail(
                f"[{label}] {len(non_accepted)} non-ACCEPTED results:\n"
                f"{json.dumps(non_accepted[:5], indent=2)}"
            )

        for rec in records:
            self.assertGreater(
                rec["absolute_score"], 0,
                f"[{label}] zero score: {rec['problem_id']} "
                f"w{rec['worker_id']} wave{rec['wave']}",
            )

        # Timing summary
        print(f"\n[concurrent] {label} per-wave timing:")
        print(f"  {'wave':>4}  {'min_s':>7}  {'max_s':>7}  {'avg_s':>7}")
        for wave in range(waves):
            wave_recs = [r for r in records if r["wave"] == wave]
            if not wave_recs:
                continue
            times = [r["elapsed_s"] for r in wave_recs]
            print(
                f"  {wave:>4}  {min(times):>7.2f}  {max(times):>7.2f}"
                f"  {sum(times) / len(times):>7.2f}"
            )

        print(f"\n[concurrent] {label}: all {len(records)} invocations passed.")

    # ------------------------------------------------------------------
    # Graduated tests  (numbered so they run in order)
    # ------------------------------------------------------------------

    def test_01_single_worker(self) -> None:
        """1 worker, 1 wave — sanity check that eval works at all."""
        workers, waves = 1, 1
        probs = PROBLEMS[:workers]
        records = self._run_concurrent_eval(workers, waves, probs, "01_1w_1wave")
        self._assert_records(records, workers, waves, "01_1w_1wave")

    def test_02_two_workers_different_problems(self) -> None:
        """2 workers evaluating different problems simultaneously."""
        workers, waves = 2, 1
        probs = PROBLEMS[:workers]
        records = self._run_concurrent_eval(workers, waves, probs, "02_2w_diff_1wave")
        self._assert_records(records, workers, waves, "02_2w_diff_1wave")

    def test_03_two_workers_same_problem(self) -> None:
        """2 workers both evaluating the same problem — first pool-expansion test."""
        workers, waves = 2, 1
        probs = ["ahc008"] * workers
        records = self._run_concurrent_eval(workers, waves, probs, "03_2w_same_1wave")
        self._assert_records(records, workers, waves, "03_2w_same_1wave")

    def test_04_five_workers_different_problems(self) -> None:
        """5 workers, each on a different problem."""
        workers, waves = 5, 1
        probs = PROBLEMS[:workers]
        records = self._run_concurrent_eval(workers, waves, probs, "04_5w_diff_1wave")
        self._assert_records(records, workers, waves, "04_5w_diff_1wave")

    def test_05_five_workers_same_problem(self) -> None:
        """5 workers all evaluating ahc008 — exercises full pool depth."""
        workers, waves = 5, 1
        probs = ["ahc008"] * workers
        records = self._run_concurrent_eval(workers, waves, probs, "05_5w_same_1wave")
        self._assert_records(records, workers, waves, "05_5w_same_1wave")

    def test_06_ten_workers_different_problems(self) -> None:
        """10 workers, one per problem, 1 wave."""
        workers, waves = 10, 1
        probs = PROBLEMS[:workers]
        records = self._run_concurrent_eval(workers, waves, probs, "06_10w_diff_1wave")
        self._assert_records(records, workers, waves, "06_10w_diff_1wave")

    def test_07_ten_workers_two_waves(self) -> None:
        """10 workers, one per problem, 2 waves."""
        workers, waves = 10, 2
        probs = PROBLEMS[:workers]
        records = self._run_concurrent_eval(workers, waves, probs, "07_10w_diff_2waves")
        self._assert_records(records, workers, waves, "07_10w_diff_2waves")

    def test_08_full_load(self) -> None:
        """10 workers, one per problem, 10 waves — full stress test."""
        workers, waves = 10, 10
        probs = PROBLEMS[:workers]
        records = self._run_concurrent_eval(workers, waves, probs, "08_10w_diff_10waves")
        self._assert_records(records, workers, waves, "08_10w_diff_10waves")


if __name__ == "__main__":
    unittest.main()
