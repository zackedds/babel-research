from __future__ import annotations

from pathlib import Path

from ..orchestration.runs import RunStore
from ..orchestration.sessions import Orchestrator
from ..orchestration.state_paths import tasks_file_path
from ..tasks.store import tasks


def inspect_run(root: Path, *, run_id: str | None = None, tmux_client=None) -> dict[str, object]:
    run_store = RunStore(root)
    runs = run_store.list_runs()
    if not runs:
        raise ValueError("No orchestration runs found")

    run = runs[-1] if run_id is None else run_store.get_run(run_id)
    if run is None:
        raise ValueError(f"Unknown run id: {run_id}")

    orchestrator = Orchestrator(root, run_id=run.id, tmux_client=tmux_client, watcher_launcher=lambda *_args: None)
    sessions = {
        session_id: session
        for session_id in [*run.planner_session_ids, *(session_id for wave in run.worker_waves for session_id in wave)]
        if (session := orchestrator.get_session(session_id)) is not None
    }
    outstanding_tasks = tasks("list", path=tasks_file_path(root, run.id), view="open", include_full=True)["result"]["tasks"]
    ready_count = sum(1 for task_item in outstanding_tasks if task_item["ready"])

    return {
        "run": _serialize_run(run),
        "planner_sessions": [_serialize_session_ref(session_id, sessions) for session_id in run.planner_session_ids],
        "worker_waves": [
            {
                "index": index,
                "status": _wave_status(wave, sessions),
                "workers": [_serialize_session_ref(session_id, sessions) for session_id in wave],
            }
            for index, wave in enumerate(run.worker_waves, start=1)
        ],
        "outstanding_tasks": {
            "total": len(outstanding_tasks),
            "ready": ready_count,
            "blocked": len(outstanding_tasks) - ready_count,
            "tasks": outstanding_tasks,
        },
    }


def _serialize_run(run) -> dict[str, object]:
    return {
        "id": run.id,
        "status": run.status,
        "current_phase": run.current_phase,
        "workdir": str(run.workdir),
        "max_rounds": run.max_rounds,
        "max_workers": run.max_workers,
        "rounds_completed": run.rounds_completed,
        "claimed_task_ids": list(run.claimed_task_ids),
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
    }


def _serialize_session_ref(session_id: str, sessions: dict[str, object]) -> dict[str, object]:
    session = sessions.get(session_id)
    if session is None:
        return {"id": session_id, "status": "missing"}
    return {
        "id": session.id,
        "tmux_session": session.tmux_session,
        "role": session.role,
        "runtime": session.runtime,
        "workdir": str(session.workdir),
        "status": session.status,
        "terminal_outcome": session.terminal_outcome,
        "started_at": session.started_at.isoformat(),
        "finished_at": session.finished_at.isoformat() if session.finished_at is not None else None,
        "failure_reason": session.failure_reason,
        "failure_source": session.failure_source,
        "last_observed_at": session.last_observed_at.isoformat() if session.last_observed_at is not None else None,
    }


def _wave_status(session_ids: list[str], sessions: dict[str, object]) -> str:
    wave_sessions = [sessions[session_id] for session_id in session_ids if session_id in sessions]
    statuses = [session.status for session in wave_sessions]
    if not statuses:
        return "missing"
    if any(session.status == "failed" or session.terminal_outcome in {"failed", "abandoned"} for session in wave_sessions):
        return "failed"
    if any(status in {"starting", "running"} for status in statuses):
        return "running"
    if all(status == "stopped" and session.terminal_outcome == "completed" for status, session in zip(statuses, wave_sessions)):
        return "completed"
    return "unknown"
