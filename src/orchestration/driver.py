from __future__ import annotations

import argparse
import secrets
import subprocess
import sys
import time
import traceback
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from ..tasks.store import tasks
from ..utils.models import AgentSession, AgentSessionSpec, OrchestrationRun
from .runs import RunStore
from .sessions import Orchestrator, append_log_line
from .state_paths import driver_log_path, tasks_file_path
from .worktree import create_worktree, ensure_branch

POLL_INTERVAL_SECONDS = 0.2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.orchestration.driver")
    parser.add_argument("--root", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--orchestrator-root", required=False, default=None)
    return parser


def spawn_orchestration_driver(root: Path, run_id: str, *, state_root: Path | None = None) -> None:
    effective_state_root = state_root or root
    log_path = driver_log_path(effective_state_root, run_id)
    append_log_line(log_path, f"spawning orchestration driver for run_id={run_id}")
    handle = log_path.open("a", encoding="utf-8")
    subprocess.Popen(
        [
            sys.executable,
            "-m",
            "src.orchestration.driver",
            "--root",
            str(effective_state_root),
            "--orchestrator-root",
            str(root),
            "--run-id",
            run_id,
        ],
        stdout=handle,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        cwd=str(root),
    )
    handle.close()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    log_path = driver_log_path(Path(args.root), args.run_id)
    try:
        append_log_line(log_path, f"driver started run_id={args.run_id}")
        orchestrator_root = Path(args.orchestrator_root) if args.orchestrator_root else Path(args.root)
        run_orchestration_loop(Path(args.root), args.run_id, orchestrator_root=orchestrator_root)
    except Exception:
        append_log_line(log_path, "driver raised an exception")
        append_log_line(log_path, traceback.format_exc().rstrip())
        return 1
    append_log_line(log_path, f"driver exited cleanly run_id={args.run_id}")
    return 0


def run_orchestration_loop(
    root: Path,
    run_id: str,
    *,
    orchestrator: Orchestrator | None = None,
    run_store: RunStore | None = None,
    poll_interval_seconds: float = POLL_INTERVAL_SECONDS,
    orchestrator_root: Path | None = None,
) -> None:
    root = root.resolve()
    run_store = run_store or RunStore(root)
    pkg_root = (orchestrator_root or root).resolve()
    orchestrator = orchestrator or Orchestrator(root, run_id=run_id, roles_path=pkg_root / "src" / "roles")
    run = run_store.get_run(run_id)
    if run is None:
        raise ValueError(f"Unknown run id: {run_id}")
    run_tasks_path = tasks_file_path(root, run.id)
    log_path = driver_log_path(root, run.id)
    append_log_line(log_path, f"orchestration loop starting phase={run.current_phase} rounds_completed={run.rounds_completed}")

    while run.status == "running":
        if run.current_phase == "planner":
            run = _ensure_planner_cycle(run, orchestrator, run_store)
            if run.status != "running":
                break
            planner = _wait_for_session(orchestrator, run.planner_session_ids[-1], poll_interval_seconds)
            if _session_failed(planner):
                append_log_line(
                    log_path,
                    f"planner session failed session_id={planner.id} outcome={planner.terminal_outcome} source={planner.failure_source}",
                )
                _update_run(run_store, replace(run, status="failed", current_phase="stopped"))
                break
            _sync_claimed_tasks(run_tasks_path, run)
            ready_tasks = tasks("list", path=run_tasks_path, view="ready", include_full=True)["result"]["tasks"]
            ready_tasks = [task_item for task_item in ready_tasks if task_item["id"] not in run.claimed_task_ids]
            if run.max_workers is not None:
                ready_tasks = ready_tasks[: run.max_workers]
            if not ready_tasks:
                append_log_line(log_path, "no ready tasks remain; marking run completed")
                _update_run(run_store, replace(run, status="completed", current_phase="stopped"))
                break
            worker_sessions: list[str] = []
            claimed_task_ids = list(run.claimed_task_ids)
            round_index = run.rounds_completed + 1
            append_log_line(log_path, f"launching worker wave round={round_index} worker_count={len(ready_tasks)}")
            for task_item in ready_tasks:
                assigned = tasks("assign", path=run_tasks_path, id=str(task_item["id"]))["result"]["task"]
                branch = str(assigned.get("branch", "")).strip()
                session_id = secrets.token_hex(4)
                worktree_path = None
                effective_workdir = run.workdir

                if branch:
                    ensure_branch(run.workdir, branch)
                    worktree_path = create_worktree(run.workdir, session_id, branch)
                    effective_workdir = worktree_path

                session_prompt = build_worker_prompt(assigned, run.id, tasks_path=run_tasks_path, branch=branch, worktree_path=worktree_path)
                worker = orchestrator.create_session(
                    AgentSessionSpec(
                        role="worker",
                        runtime="codex",
                        session_prompt=session_prompt,
                        workdir=effective_workdir,
                        round_index=round_index,
                        task_id=str(assigned["id"]),
                        task_title=str(assigned.get("title", "")).strip() or None,
                        task_description=str(assigned.get("description", "")).strip() or None,
                        session_id=session_id,
                        worktree_path=worktree_path,
                    )
                )
                append_log_line(
                    log_path,
                    f"worker session created session_id={worker.id} task_id={assigned['id']} title={assigned.get('title', '')}",
                )
                worker_sessions.append(worker.id)
                claimed_task_ids.append(str(assigned["id"]))
            run = _update_run(
                run_store,
                replace(
                    run,
                    worker_waves=[*run.worker_waves, worker_sessions],
                    claimed_task_ids=claimed_task_ids,
                    current_phase="worker",
                ),
            )
            continue

        if run.current_phase == "worker":
            current_wave = run.worker_waves[-1] if run.worker_waves else []
            workers = [_wait_for_session(orchestrator, session_id, poll_interval_seconds) for session_id in current_wave]
            if any(_session_failed(worker) for worker in workers):
                failed_worker = next(worker for worker in workers if _session_failed(worker))
                append_log_line(
                    log_path,
                    f"worker session failed session_id={failed_worker.id} outcome={failed_worker.terminal_outcome} source={failed_worker.failure_source}",
                )
                _update_run(run_store, replace(run, status="failed", current_phase="stopped"))
                break
            rounds_completed = run.rounds_completed + 1
            if run.max_rounds is not None and rounds_completed >= run.max_rounds:
                append_log_line(log_path, f"max rounds reached at round={rounds_completed}; marking run completed")
                _update_run(
                    run_store,
                    replace(run, rounds_completed=rounds_completed, status="completed", current_phase="stopped"),
                )
                break
            append_log_line(log_path, f"worker wave completed; advancing to planner round={rounds_completed + 1}")
            run = _update_run(run_store, replace(run, rounds_completed=rounds_completed, current_phase="planner"))
            continue

        raise ValueError(f"Unsupported run phase: {run.current_phase}")

    return None


def _ensure_planner_cycle(run: OrchestrationRun, orchestrator: Orchestrator, run_store: RunStore) -> OrchestrationRun:
    if run.current_phase != "planner":
        return run
    if run.planner_session_ids:
        last = orchestrator.get_session(run.planner_session_ids[-1])
        if last is not None and last.status in {"running", "starting"}:
            return run
    tasks("prepare", path=tasks_file_path(orchestrator.root, run.id))
    planner = orchestrator.create_session(
        AgentSessionSpec(
            role="planner",
            runtime="codex",
            session_prompt=build_planner_prompt(run.initial_prompt, run.id),
            workdir=run.workdir,
            template_vars={"max_workers": run.max_workers if run.max_workers is not None else "unlimited"},
            round_index=run.rounds_completed + 1,
        )
    )
    return _update_run(run_store, replace(run, planner_session_ids=[*run.planner_session_ids, planner.id]))


def _wait_for_session(orchestrator: Orchestrator, session_id: str, poll_interval_seconds: float) -> AgentSession:
    while True:
        session = orchestrator.get_session(session_id)
        if session is None:
            raise ValueError(f"Unknown session id: {session_id}")
        if session.status not in {"starting", "running"}:
            return session
        time.sleep(poll_interval_seconds)


def build_planner_prompt(initial_prompt: str, run_id: str) -> str:
    return "\n".join(
        [
            f"Run ID: {run_id}",
            "",
            initial_prompt.strip(),
            "",
            "Planner completion checklist:",
            "1. Create all requested tasks and notes in this run.",
            "2. Write any requested planner-side artifacts.",
            f"3. Inspect the run task state: tasks --run-id {run_id} list --view all --include-full",
            f"4. Final required command for success: tasks --run-id {run_id} ready",
            "5. Do not stop before step 4 has succeeded.",
            "",
            "Execution constraints:",
            "- Do not run help commands or broad repo exploration unless blocked.",
            "- Use direct task mutations immediately; the required work is already specified.",
        ]
    ).strip()


def build_worker_prompt(task_item: dict[str, object], run_id: str, *, tasks_path: Path | None = None, branch: str | None = None, worktree_path: Path | None = None) -> str:
    task_id = str(task_item.get("id", "")).strip()
    tasks_flag = f"--tasks-path {tasks_path}" if tasks_path is not None else f"--run-id {run_id}"
    notes = task_item.get("notes", [])
    note_lines: list[str] = []
    if isinstance(notes, list):
        for note in notes:
            if isinstance(note, dict):
                text = str(note.get("text", "")).strip()
                if text:
                    note_lines.append(f"- {text}")
    if not note_lines:
        note_lines = ["- none"]
    lines = [
        f"Task ID: {task_id}",
        f"Run ID: {run_id}",
        "",
        str(task_item.get("title", "")).strip(),
        "",
        str(task_item.get("description", "")).strip(),
        "",
        "Task commands:",
        f"- Read: tasks {tasks_flag} get --id {task_id}",
        f'- Note: tasks {tasks_flag} note-append --id {task_id} --note "<progress>"',
        f'- Close: tasks {tasks_flag} close --id {task_id} --reason "<result>"',
        "",
        "Worker completion checklist:",
        "1. Read the assignment and implement the requested work.",
        "2. Verify the behavior you changed.",
        f'3. Final required command for success: tasks {tasks_flag} close --id {task_id} --reason "<result>"',
        "4. Do not stop before step 3 has succeeded.",
        "",
        "Notes:",
        *note_lines,
    ]
    if branch and worktree_path is not None:
        lines += [
            "",
            f"Branch context: {branch}",
            f"Working directory: {worktree_path}",
            f"Your work is on a private sub-branch. Decide whether to merge into '{branch}' before closing.",
        ]
    return "\n".join(lines).strip()


def _session_failed(session: AgentSession) -> bool:
    return session.status == "failed" or session.terminal_outcome != "completed"


def _update_run(run_store: RunStore, run: OrchestrationRun) -> OrchestrationRun:
    return run_store.update_run(replace(run, updated_at=datetime.now(UTC)))


def _sync_claimed_tasks(tasks_path: Path, run: OrchestrationRun) -> None:
    for task_id in run.claimed_task_ids:
        task_item = tasks("get", path=tasks_path, id=task_id)["result"]
        if task_item["status"] == "open":
            tasks("assign", path=tasks_path, id=task_id)


if __name__ == "__main__":
    raise SystemExit(main())
