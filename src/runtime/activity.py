from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..orchestration.runs import RunStore
from ..orchestration.sessions import Orchestrator
from ..utils.models import AgentSession


def activity_snapshot(root: Path, *, run_id: str | None = None, tmux_client=None) -> dict[str, object]:
    run_store = RunStore(root)
    runs = run_store.list_runs()
    if not runs:
        raise ValueError("No orchestration runs found")

    run = runs[-1] if run_id is None else run_store.get_run(run_id)
    if run is None:
        raise ValueError(f"Unknown run id: {run_id}")

    orchestrator = Orchestrator(root, run_id=run.id, tmux_client=tmux_client, watcher_launcher=lambda *_args: None)
    active_ids = set(run.planner_session_ids)
    for wave in run.worker_waves:
        active_ids.update(wave)
    active_ids.update(run.librarian_session_ids)

    rows = []
    for session_id in active_ids:
        session = orchestrator.get_session(session_id)
        if session is not None:
            rows.append(_serialize_activity_session(session))
    running = sorted(
        [row for row in rows if row["display_status"] == "running"],
        key=lambda item: item["started_at"],
        reverse=True,
    )
    history = sorted(
        [row for row in rows if row["display_status"] != "running"],
        key=lambda item: (item["finished_at"] or "", item["started_at"]),
        reverse=True,
    )
    return {
        "run": {
            "id": run.id,
            "status": run.status,
            "current_phase": run.current_phase,
            "rounds_completed": run.rounds_completed,
            "created_at": run.created_at.isoformat(),
            "updated_at": run.updated_at.isoformat(),
        },
        "running": running,
        "history": history,
        "sessions": [*running, *history],
    }


def _serialize_activity_session(session: AgentSession) -> dict[str, object]:
    terminal_outcome = session.terminal_outcome
    if session.status in {"starting", "running"}:
        display_status = "running"
    elif terminal_outcome == "completed":
        display_status = "finished"
    else:
        display_status = "failed"
    return {
        "id": session.id,
        "tmux_session": session.tmux_session,
        "role": session.role,
        "status": session.status,
        "display_status": display_status,
        "terminal_outcome": terminal_outcome,
        "round_index": session.round_index,
        "task_id": session.task_id,
        "task_title": session.task_title,
        "task_description": session.task_description,
        "started_at": session.started_at.isoformat(),
        "finished_at": session.finished_at.isoformat() if session.finished_at is not None else None,
        "failure_reason": session.failure_reason,
        "failure_source": session.failure_source,
        "last_observed_at": session.last_observed_at.isoformat() if session.last_observed_at is not None else None,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.runtime.activity")
    parser.add_argument("--root", required=True)
    parser.add_argument("--run-id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = activity_snapshot(Path(args.root), run_id=args.run_id)
    except Exception as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
