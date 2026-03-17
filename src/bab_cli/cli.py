from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from ..orchestration.driver import spawn_orchestration_driver
from ..orchestration.runs import RunStore
from ..orchestration.sessions import Orchestrator
from ..runtime.activity import activity_snapshot
from ..runtime.inspect import inspect_run


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bab")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run")
    run.add_argument("prompt_file", help="Path to the session prompt file")
    run.add_argument("--max-rounds", type=_positive_int, default=None)
    run.add_argument("--max-workers", type=_positive_int, default=None)

    inspect = subparsers.add_parser("inspect")
    inspect.add_argument("--run-id", help="Run id to inspect; defaults to the latest run")

    activity = subparsers.add_parser("activity")
    activity.add_argument("--run-id", help="Run id to inspect; defaults to the latest run")
    activity.add_argument("--snapshot", action="store_true", help=argparse.SUPPRESS)

    kill = subparsers.add_parser("kill")
    kill.add_argument("run_id", help="Run id to kill")
    return parser


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


def run_file(
    prompt_file: Path,
    cwd: Path,
    *,
    root: Path | None = None,
    max_rounds: int | None = None,
    max_workers: int | None = None,
) -> dict[str, object]:
    prompt_path = prompt_file.resolve()
    prompt_text = prompt_path.read_text(encoding="utf-8")

    orchestrator_root = root or _repo_root()
    state_root = cwd.resolve()
    run_store = RunStore(state_root)
    run = run_store.create_run(
        initial_prompt=prompt_text,
        workdir=state_root,
        max_rounds=max_rounds,
        max_workers=max_workers,
    )
    spawn_orchestration_driver(orchestrator_root, run.id, state_root=state_root)
    return {"ok": True, "run_id": run.id}


def run_activity(
    cwd: Path,
    *,
    root: Path | None = None,
    run_id: str | None = None,
    snapshot: bool = False,
) -> dict[str, object] | int:
    orchestrator_root = root or _repo_root()
    state_root = cwd.resolve()
    if snapshot:
        return {"ok": True, "op": "activity_snapshot", "result": activity_snapshot(state_root, run_id=run_id)}

    bun = shutil.which("bun")
    if bun is None:
        raise RuntimeError("bun is required for `bab activity`")

    entrypoint = orchestrator_root / "src" / "tui" / "activity-feed" / "src" / "index.tsx"
    if not entrypoint.exists():
        raise RuntimeError(f"activity TUI entrypoint not found: {entrypoint}")

    command = [bun, "run", str(entrypoint), "--root", str(state_root), "--package-root", str(orchestrator_root)]
    if run_id is not None:
        command.extend(["--run-id", run_id])
    command.extend(["--python", sys.executable])
    completed = subprocess.run(command, cwd=str(orchestrator_root))
    return completed.returncode


def kill_run(run_id: str, cwd: Path) -> dict[str, object]:
    state_root = cwd.resolve()
    run_store = RunStore(state_root)
    run = run_store.get_run(run_id)
    if run is None:
        raise ValueError(f"Unknown run id: {run_id}")
    if run.status != "running":
        return {"ok": True, "run_id": run_id, "note": f"run is already {run.status}"}
    orchestrator = Orchestrator(state_root, run_id=run_id)
    all_session_ids = (
        run.planner_session_ids
        + [sid for wave in run.worker_waves for sid in wave]
        + run.librarian_session_ids
    )
    for session_id in all_session_ids:
        orchestrator.kill_session(session_id)
    return {"ok": True, "run_id": run_id, "sessions_killed": len(all_session_ids)}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in {"run", "inspect", "activity", "kill", "-h", "--help"}:
        argv = ["run", *argv]
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "run":
            result = run_file(
                Path(args.prompt_file),
                Path.cwd(),
                max_rounds=args.max_rounds,
                max_workers=args.max_workers,
            )
        elif args.command == "activity":
            result = run_activity(Path.cwd(), run_id=args.run_id, snapshot=args.snapshot)
        elif args.command == "kill":
            result = kill_run(args.run_id, Path.cwd())
        else:
            result = {"ok": True, "op": "inspect", "result": inspect_run(Path.cwd(), run_id=args.run_id)}
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.command == "activity" and not args.snapshot:
        return int(result)

    if args.command == "run":
        run_id = result["run_id"]
        print(f"{run_id} run started")
        print(f"Monitor run with: bab activity {run_id}")
        return 0

    if args.command == "kill":
        run_id = result["run_id"]
        print(f"{run_id} killed")
        return 0

    print(__import__("json").dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
