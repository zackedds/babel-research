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
from .worktree import create_worktree, ensure_branch, remove_worktree

POLL_INTERVAL_SECONDS = 0.2


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Parse YAML frontmatter from a markdown file (key: value lines between --- delimiters)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    result: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" in line:
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip()
    return result


def build_wiki_toc(wiki_root: Path, max_depth: int = 3) -> list[dict]:
    """Walk wiki_root up to max_depth levels; return nested list of dicts for dirs with MAIN.md."""

    def _walk(directory: Path, depth: int) -> list[dict]:
        if depth > max_depth:
            return []
        entries = []
        try:
            children = sorted(p for p in directory.iterdir() if p.is_dir())
        except PermissionError:
            return []
        for child in children:
            main_md = child / "MAIN.md"
            if not main_md.exists():
                continue
            try:
                fm = _parse_frontmatter(main_md.read_text(encoding="utf-8"))
            except OSError:
                fm = {}
            rel_path = child.relative_to(wiki_root)
            entries.append(
                {
                    "path": str(rel_path),
                    "name": fm.get("name", child.name),
                    "description": fm.get("description", ""),
                    "entries": _walk(child, depth + 1),
                }
            )
        return entries

    if not wiki_root.is_dir():
        return []
    return _walk(wiki_root, 1)


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
    _ensure_wiki_dir(run.workdir)

    while run.status == "running":
        if run.current_phase == "planner":
            _max_planner_attempts = 2
            planner_succeeded = False
            for planner_attempt in range(1, _max_planner_attempts + 1):
                run = _ensure_planner_cycle(run, orchestrator, run_store)
                if run.status != "running":
                    break
                planner = _wait_for_session(orchestrator, run.planner_session_ids[-1], poll_interval_seconds)
                if _session_failed(planner):
                    append_log_line(
                        log_path,
                        f"planner session failed session_id={planner.id} outcome={planner.terminal_outcome} source={planner.failure_source} (attempt {planner_attempt}/{_max_planner_attempts})",
                    )
                    if planner_attempt < _max_planner_attempts:
                        append_log_line(log_path, "retrying planner...")
                        continue
                    _update_run(run_store, replace(run, status="failed", current_phase="stopped"))
                    break
                planner_succeeded = True
                break
            if not planner_succeeded:
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

                session_prompt = build_worker_prompt(assigned, run.id, branch=branch, worktree_path=worktree_path)
                worker = orchestrator.create_session(
                    AgentSessionSpec(
                        role="worker",
                        runtime=orchestrator.config.agent_runtime,
                        session_prompt=session_prompt,
                        workdir=effective_workdir,
                        round_index=round_index,
                        task_id=str(assigned["id"]),
                        task_title=str(assigned.get("title", "")).strip() or None,
                        task_description=str(assigned.get("description", "")).strip() or None,
                        session_id=session_id,
                        worktree_path=worktree_path,
                        template_vars={
                            "run_id": run.id,
                            "task": assigned,
                            "notes": format_task_notes(assigned),
                            "wiki_sections": build_wiki_toc(Path(run.workdir) / "wiki"),
                        },
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
            completed_workers = [w for w in workers if not _session_failed(w)]
            failed_workers = [w for w in workers if _session_failed(w)]
            for fw in failed_workers:
                append_log_line(
                    log_path,
                    f"worker session failed session_id={fw.id} outcome={fw.terminal_outcome} source={fw.failure_source} (continuing with remaining workers)",
                )
            if not completed_workers:
                append_log_line(log_path, "all workers in wave failed; marking run failed")
                _update_run(run_store, replace(run, status="failed", current_phase="stopped"))
                break
            append_log_line(log_path, f"worker wave finished: {len(completed_workers)} completed, {len(failed_workers)} failed")
            for worker in completed_workers:
                if worker.task_id:
                    task_item = tasks("get", path=run_tasks_path, id=worker.task_id)["result"]
                    branch = str(task_item.get("branch", "")).strip()
                    if branch:
                        wiki_paths = _merge_wiki_from_worker(run.workdir, worker, branch, log_path)
                        if wiki_paths:
                            orchestrator._upsert(replace(worker, wiki_file_paths=wiki_paths))
            tasks("wiki_under_construction", path=run_tasks_path)
            round_index = run.rounds_completed + 1
            closed_tasks = tasks("list", path=run_tasks_path, view="closed", include_full=True)["result"]["tasks"]
            all_sessions = {s.task_id: s for s in orchestrator.list_sessions() if s.task_id}
            template_tasks = [
                {
                    "description": t["description"],
                    "status": t["status"],
                    "wiki_paths": (
                        list(t.get("wiki_file_paths") or [])
                        or list(getattr(all_sessions.get(t["id"]), "wiki_file_paths", None) or [])
                    ),
                }
                for t in closed_tasks
            ]
            librarian = orchestrator.create_session(
                AgentSessionSpec(
                    role="librarian",
                    runtime=orchestrator.config.agent_runtime,
                    session_prompt=build_librarian_prompt(run.id),
                    workdir=run.workdir,
                    round_index=round_index,
                    template_vars={
                        "run_id": run.id,
                        "max_workers": run.max_workers if run.max_workers is not None else "unlimited",
                        "tasks": template_tasks,
                        "user_prompt": run.initial_prompt,
                        "wiki_sections": build_wiki_toc(Path(run.workdir) / "wiki"),
                    },
                )
            )
            append_log_line(log_path, f"librarian session created session_id={librarian.id} round={round_index}")
            run = _update_run(
                run_store,
                replace(run, librarian_session_ids=[*run.librarian_session_ids, librarian.id], current_phase="librarian"),
            )
            continue

        if run.current_phase == "librarian":
            librarian = _wait_for_session(orchestrator, run.librarian_session_ids[-1], poll_interval_seconds)
            if _session_failed(librarian):
                append_log_line(
                    log_path,
                    f"librarian session failed session_id={librarian.id} outcome={librarian.terminal_outcome} source={librarian.failure_source} (continuing to next round)",
                )
            current_wave = run.worker_waves[-1] if run.worker_waves else []
            for session_id in current_wave:
                session = orchestrator.get_session(session_id)
                if session and session.task_id:
                    task_item = tasks("get", path=run_tasks_path, id=session.task_id)["result"]
                    branch = str(task_item.get("branch", "")).strip()
                    if branch:
                        _sync_wiki_to_branch(run.workdir, branch, log_path)
            rounds_completed = run.rounds_completed + 1
            if run.max_rounds is not None and rounds_completed >= run.max_rounds:
                append_log_line(log_path, f"max rounds reached at round={rounds_completed}; marking run completed")
                _update_run(
                    run_store,
                    replace(run, rounds_completed=rounds_completed, status="completed", current_phase="stopped"),
                )
                break
            append_log_line(log_path, f"librarian complete; advancing to planner round={rounds_completed + 1}")
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
    from jinja2 import Environment as _JinjaEnv

    run_tasks_path = tasks_file_path(orchestrator.root, run.id)
    tasks("prepare", path=run_tasks_path)

    closed_tasks = tasks("list", path=run_tasks_path, view="closed", include_full=True)["result"]["tasks"]
    all_sessions = {s.task_id: s for s in orchestrator.list_sessions() if s.task_id}
    template_tasks = [
        {
            "description": t["description"],
            "status": t["status"],
            "wiki_paths": (
                list(t.get("wiki_file_paths") or [])
                or list(getattr(all_sessions.get(t["id"]), "wiki_file_paths", None) or [])
            ),
        }
        for t in closed_tasks
    ]

    _jinja = _JinjaEnv(keep_trailing_newline=True)
    rendered_user_prompt = _jinja.from_string(run.initial_prompt).render(
        tasks=template_tasks,
        wiki_sections=build_wiki_toc(Path(run.workdir) / "wiki"),
        max_workers=run.max_workers if run.max_workers is not None else "unlimited",
    )

    planner = orchestrator.create_session(
        AgentSessionSpec(
            role="planner",
            runtime=orchestrator.config.agent_runtime,
            session_prompt=build_planner_prompt(run.initial_prompt, run.id),
            workdir=run.workdir,
            template_vars={
                "run_id": run.id,
                "max_workers": run.max_workers if run.max_workers is not None else "unlimited",
                "tasks": template_tasks,
                "user_prompt": rendered_user_prompt,
                "wiki_sections": build_wiki_toc(Path(run.workdir) / "wiki"),
            },
            round_index=run.rounds_completed + 1,
        )
    )
    return _update_run(run_store, replace(run, planner_session_ids=[*run.planner_session_ids, planner.id]))


def _wait_for_session(
    orchestrator: Orchestrator,
    session_id: str,
    poll_interval_seconds: float,
    timeout_seconds: float = 1800.0,
) -> AgentSession:
    deadline = time.monotonic() + timeout_seconds
    while True:
        session = orchestrator.get_session(session_id)
        if session is None:
            raise ValueError(f"Unknown session id: {session_id}")
        if session.status not in {"starting", "running"}:
            return session
        if time.monotonic() > deadline:
            return replace(
                session,
                status="stopped",
                terminal_outcome="failed",
                failure_reason=f"session timed out after {timeout_seconds}s",
                failure_source="driver_timeout",
            )
        time.sleep(poll_interval_seconds)


def build_planner_prompt(initial_prompt: str, run_id: str) -> str:
    return "\n".join(
        [
            f"Run ID: {run_id}",
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


def format_task_notes(task_item: dict[str, object]) -> str:
    notes = task_item.get("notes", [])
    note_lines: list[str] = []
    if isinstance(notes, list):
        for note in notes:
            if isinstance(note, dict):
                text = str(note.get("text", "")).strip()
                if text:
                    note_lines.append(text)
    return "\n\n".join(note_lines) if note_lines else "(none)"


def build_worker_prompt(task_item: dict[str, object], run_id: str, *, branch: str | None = None, worktree_path: Path | None = None) -> str:
    task_id = str(task_item.get("id", "")).strip()
    tasks_flag = f"--run-id {run_id}"
    note_lines = [f"- {line}" for line in format_task_notes(task_item).splitlines() if line.strip()] or ["- none"]
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
            f"Branch: {branch}",
            f"Working directory: {worktree_path}",
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


def _ensure_wiki_dir(workdir: Path) -> None:
    """Create wiki/ in workdir and commit it if it doesn't already exist."""
    wiki_dir = workdir / "wiki"
    wiki_dir.mkdir(exist_ok=True)
    gitkeep = wiki_dir / ".gitkeep"
    if not gitkeep.exists():
        gitkeep.touch()
    result = subprocess.run(
        ["git", "status", "--porcelain", "wiki/"],
        capture_output=True,
        text=True,
        cwd=workdir,
    )
    if result.returncode == 0 and result.stdout.strip():
        subprocess.run(["git", "add", "wiki/"], capture_output=True, check=True, cwd=workdir)
        subprocess.run(
            ["git", "commit", "-m", "chore: init wiki directory"],
            capture_output=True,
            check=True,
            cwd=workdir,
        )


def _merge_wiki_from_worker(workdir: Path, session: AgentSession, task_branch: str, log_path: Path) -> tuple[str, ...]:
    """Checkout wiki/ from task_branch onto main (workdir). Commit if changed."""
    result = subprocess.run(
        ["git", "checkout", task_branch, "--", "wiki/"],
        capture_output=True,
        cwd=workdir,
    )
    if result.returncode != 0:
        append_log_line(log_path, f"wiki merge skipped for session={session.id} branch={task_branch}: {result.stderr.decode().strip()}")
        return ()
    status = subprocess.run(
        ["git", "status", "--porcelain", "wiki/"],
        capture_output=True,
        text=True,
        cwd=workdir,
    )
    changed_paths: tuple[str, ...] = ()
    if status.returncode == 0 and status.stdout.strip():
        lines = status.stdout.strip().splitlines()
        changed_paths = tuple(
            line[3:].strip()
            for line in lines
            if len(line) > 3 and line[:2].strip() not in ("D", "DD")
        )
        subprocess.run(["git", "add", "wiki/"], capture_output=True, check=True, cwd=workdir)
        subprocess.run(
            ["git", "commit", "-m", f"wiki: merge from {session.task_id}"],
            capture_output=True,
            check=True,
            cwd=workdir,
        )
        append_log_line(log_path, f"wiki merge committed for session={session.id} branch={task_branch}")
    else:
        append_log_line(log_path, f"wiki merge: no wiki changes for session={session.id} branch={task_branch}")
    return changed_paths


def _sync_wiki_to_branch(workdir: Path, task_branch: str, log_path: Path) -> None:
    """Copy wiki/ from main into task_branch via a temporary worktree."""
    temp_id = secrets.token_hex(4)
    temp_path = workdir / ".worktrees" / f"wiki-sync-{temp_id}"
    try:
        subprocess.run(
            ["git", "worktree", "add", str(temp_path), task_branch],
            capture_output=True,
            check=True,
            cwd=workdir,
        )
        subprocess.run(
            ["git", "checkout", "main", "--", "wiki/"],
            capture_output=True,
            cwd=temp_path,
        )
        status = subprocess.run(
            ["git", "status", "--porcelain", "wiki/"],
            capture_output=True,
            text=True,
            cwd=temp_path,
        )
        if status.returncode == 0 and status.stdout.strip():
            subprocess.run(["git", "add", "wiki/"], capture_output=True, check=True, cwd=temp_path)
            subprocess.run(
                ["git", "commit", "-m", "wiki: sync from main"],
                capture_output=True,
                check=True,
                cwd=temp_path,
            )
            append_log_line(log_path, f"wiki synced to branch={task_branch}")
        else:
            append_log_line(log_path, f"wiki sync: no changes to push to branch={task_branch}")
    except Exception as exc:
        append_log_line(log_path, f"wiki sync failed for branch={task_branch}: {exc}")
    finally:
        remove_worktree(workdir, f"wiki-sync-{temp_id}")


def build_librarian_prompt(run_id: str) -> str:
    return "\n".join(
        [
            f"Run ID: {run_id}",
            "",
            "Librarian completion checklist:",
            "1. Review all files in wiki/",
            "2. Organize and improve wiki content",
            f"3. For each task in the context whose wiki content is known, call: tasks --run-id {run_id} catalog --id <task-id> --paths <wiki/... paths after reorganisation>. Only use task IDs that appear in the tasks context you received (these are closed tasks); do not invent or use other task IDs.",
            f"4. Final required command for success: tasks --run-id {run_id} wiki-ready",
            "5. Do not stop before step 4 has succeeded.",
            "",
            "Execution constraints:",
            "- Do not modify files outside wiki/. Running tasks CLI commands is required and permitted.",
            "- Do not delete substantive content, only reorganize.",
        ]
    ).strip()


if __name__ == "__main__":
    raise SystemExit(main())
