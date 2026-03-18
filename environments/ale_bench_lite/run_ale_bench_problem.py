#!/usr/bin/env python3
"""Run a single ALE-Bench problem using the bab orchestrator.

Usage:
    uv run environments/ale_bench_lite/run_ale_bench_problem.py --problem-id ahc008
    uv run environments/ale_bench_lite/run_ale_bench_problem.py --problem-id ahc008 --max-rounds 4 --max-workers 3
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import ale_bench
import yaml
from jinja2 import Template

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
DEFAULT_CONFIG = SCRIPT_DIR / "ale_bench_lite.yaml"
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "runs"
SERVER_PORT = 8765


def load_config(config_path: Path) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


def get_problem_statement(problem_id: str) -> str:
    session = ale_bench.start(
        problem_id=problem_id,
        lite_version=False,
        num_workers=1,
        run_visualization_server=False,
    )
    try:
        return session.problem.statement
    finally:
        session.close()


def append_problem_limits(statement: str, time_limit: str, memory_limit: str) -> str:
    return (
        f"{statement}\n\n"
        f"## Judge Constraints\n"
        f"- Time Limit: {time_limit}\n"
        f"- Memory Limit: {memory_limit}\n"
    )


def ensure_server_running() -> None:
    url = f"http://localhost:{SERVER_PORT}/health"
    try:
        urllib.request.urlopen(url, timeout=2)
        print(f"[run] ALE-Bench server already running on port {SERVER_PORT}")
        return
    except Exception:
        pass

    print(f"[run] Starting ALE-Bench server on port {SERVER_PORT}...")
    subprocess.Popen(
        [sys.executable, str(SCRIPT_DIR / "server.py")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Wait for server to be ready
    for _ in range(15):
        time.sleep(1)
        try:
            urllib.request.urlopen(url, timeout=2)
            print(f"[run] Server ready on port {SERVER_PORT}")
            return
        except Exception:
            pass
    print(f"[run] WARNING: server may not be ready on port {SERVER_PORT}")


def start_container(
    container_name: str,
    run_dir: Path,
    image: str,
) -> None:
    codex_dir = run_dir / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)

    # Write babel-agent config with gpt-5 for all roles
    babel_agent_dir = run_dir / ".babel-agent"
    babel_agent_dir.mkdir(parents=True, exist_ok=True)
    (babel_agent_dir / "config.toml").write_text(
        '[roles.planner]\nmodel = "gpt-5"\n\n'
        '[roles.worker]\nmodel = "gpt-5"\n\n'
        '[roles.librarian]\nmodel = "gpt-5"\n'
    )
    host_auth = Path.home() / ".codex" / "auth.json"
    if host_auth.exists():
        shutil.copy2(host_auth, codex_dir / "auth.json")

    cmd = [
        "docker", "run", "-d",
        "--name", container_name,
        "-v", f"{run_dir}:/testbed/workspace",
        "-v", f"{run_dir / '.codex'}:/root/.codex",
        "-v", f"{REPO_ROOT}:/opt/babel-research",
        "--add-host", "host.docker.internal:host-gateway",
        image,
        "sleep", "infinity",
    ]
    print(f"[run] Starting container: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)
    subprocess.run(
        ["docker", "exec", container_name, "pip", "install", "-q", "-e", "/opt/babel-research"],
        check=True,
    )
    subprocess.run(
        ["docker", "exec", container_name,
         "git", "config", "--global", "--add", "safe.directory", "/testbed/workspace"],
        check=True,
    )
    # Init git repo with an initial commit so branches/worktrees work
    (run_dir / ".gitignore").write_text(".codex/\n")
    subprocess.run(
        ["docker", "exec", "-w", "/testbed/workspace", container_name, "git", "init"],
        check=True,
    )
    subprocess.run(
        ["docker", "exec", "-w", "/testbed/workspace", container_name,
         "git", "config", "user.email", "bab@ale-bench.local"],
        check=True,
    )
    subprocess.run(
        ["docker", "exec", "-w", "/testbed/workspace", container_name,
         "git", "config", "user.name", "Bab Runner"],
        check=True,
    )
    subprocess.run(
        ["docker", "exec", "-w", "/testbed/workspace", container_name, "git", "add", "."],
        check=True,
    )
    subprocess.run(
        ["docker", "exec", "-w", "/testbed/workspace", container_name,
         "git", "commit", "-m", "initial"],
        check=True,
    )


def stop_container(container_name: str) -> None:
    print(f"[run] Stopping container {container_name}")
    subprocess.run(["docker", "stop", container_name], check=False)
    subprocess.run(["docker", "rm", container_name], check=False)


def run_bab_in_container(
    container_name: str,
    run_dir: Path,
    max_rounds: int | None,
    max_workers: int | None,
) -> None:
    from src.orchestration.runs import RunStore

    cmd = ["docker", "exec", "-w", "/testbed/workspace", container_name,
           "bab", "run", "/testbed/workspace/prompt.md"]
    if max_rounds is not None:
        cmd += ["--max-rounds", str(max_rounds)]
    if max_workers is not None:
        cmd += ["--max-workers", str(max_workers)]
    print(f"[run] Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    run_id = result.stdout.split()[0]
    print(f"[run] run_id={run_id}, polling for completion...")

    store = RunStore(run_dir)
    driver_log = run_dir / ".babel-agent" / "runs" / run_id / "logs" / "driver.log"
    deadline = time.monotonic() + 4 * 60 * 60  # 4 hour timeout
    while time.monotonic() < deadline:
        time.sleep(10)
        run = store.get_run(run_id)
        if run is None:
            continue
        if run.status == "completed":
            print(f"[run] run {run_id} completed")
            return
        if run.status == "failed":
            log_tail = driver_log.read_text(encoding="utf-8")[-2000:] if driver_log.exists() else "(no driver log)"
            raise RuntimeError(f"run {run_id} failed\n\ndriver.log tail:\n{log_tail}")
        if driver_log.exists():
            log_text = driver_log.read_text(encoding="utf-8")
            if "driver raised an exception" in log_text:
                raise RuntimeError(f"run {run_id} driver crashed\n\n{log_text[-2000:]}")
    log_tail = driver_log.read_text(encoding="utf-8")[-2000:] if driver_log.exists() else "(no driver log)"
    raise TimeoutError(f"run {run_id} timed out\n\ndriver.log tail:\n{log_tail}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a single ALE-Bench problem with bab")
    parser.add_argument("--problem-id", required=True, help="Problem ID (e.g. ahc008)")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Path to YAML config")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Output directory")
    parser.add_argument("--max-rounds", type=int, default=None)
    parser.add_argument("--max-workers", type=int, default=None)
    args = parser.parse_args()

    problem_id: str = args.problem_id
    config_path: Path = args.config
    output_dir: Path = args.output_dir
    max_rounds: int | None = args.max_rounds
    max_workers: int | None = args.max_workers

    # Load config
    config = load_config(config_path)

    if max_rounds is None:
        max_rounds = config.get("bab", {}).get("max_rounds")
    if max_workers is None:
        max_workers = config.get("bab", {}).get("max_workers")

    problem_limits = config["problem_limits"].get(problem_id, {})
    time_limit = problem_limits.get("time_limit", "2 sec")
    memory_limit = problem_limits.get("memory_limit", "1024 MiB")
    initial_score = config["initial_solution_public_scores"].get(problem_id, "unknown")
    image = config["docker"]["image"]

    # Get problem statement
    print(f"[run] Fetching problem statement for {problem_id}...")
    statement = get_problem_statement(problem_id)
    augmented_statement = append_problem_limits(statement, time_limit, memory_limit)

    # Render prompt template
    template = Template(config["prompt_template"])
    prompt_text = template.render(
        problem_id=problem_id,
        problem_description=augmented_statement,
        initial_score=initial_score,
        time_limit=time_limit,
        memory_limit=memory_limit,
        allowed_languages=config["allowed_languages"],
        allowed_cpp_libraries=config["allowed_cpp_libraries"],
        allowed_python_libraries=config["allowed_python_libraries"],
        allowed_rust_libraries=config["allowed_rust_libraries"],
    )

    # Create timestamped run directory
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = output_dir / f"{problem_id}_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"[run] Run directory: {run_dir}")

    # Copy baseline solution
    baseline_src = SCRIPT_DIR / "ale_agent_best" / f"{problem_id}.cpp"
    baseline_dst = run_dir / "baseline.cpp"
    shutil.copy2(baseline_src, baseline_dst)
    print(f"[run] Copied baseline: {baseline_src} -> {baseline_dst}")

    # Write prompt
    prompt_file = run_dir / "prompt.md"
    prompt_file.write_text(prompt_text, encoding="utf-8")
    print(f"[run] Wrote prompt: {prompt_file}")

    # Write baseline context (shown to planner only on round 1)
    if "baseline_context_template" in config:
        baseline_context = Template(config["baseline_context_template"]).render(
            initial_score=initial_score,
        )
        (run_dir / "baseline_context.md").write_text(baseline_context, encoding="utf-8")

    # Ensure host eval server is running
    ensure_server_running()

    # Start Docker container
    container_name = f"ale-bench-{problem_id}-{timestamp}"
    start_container(container_name, run_dir.resolve(), image)

    try:
        # Run bab orchestration inside container
        run_bab_in_container(container_name, run_dir, max_rounds, max_workers)
    finally:
        stop_container(container_name)


if __name__ == "__main__":
    main()
