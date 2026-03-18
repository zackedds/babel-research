from __future__ import annotations

import secrets
import shutil
import subprocess
import sys
import threading
import time
import traceback

import jinja2
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

from ..roles.registry import RoleRegistryError, load_roles
from .worktree import remove_worktree
from ..runtime.runtimes import get_runtime
from ..runtime.tmux import TmuxClient
from ..tasks.store import tasks
from ..utils.config import BabelConfig
from ..utils.json_store import load_json, locked_json_store, write_json_atomic
from ..utils.models import AgentSession, AgentSessionSpec
from .state_paths import LEGACY_SESSIONS_PATH, session_log_path, sessions_file_path, tasks_file_path

STATE_POLL_INTERVAL_SECONDS = 0.2
DEFAULT_SESSIONS_PATH = LEGACY_SESSIONS_PATH
SESSION_MARKER_INSTRUCTIONS = (
    "Session completion contract:\n"
    "- Planner sessions complete only after they mark the run ready through the tasks CLI.\n"
    "- Worker sessions complete only after they close their assigned task through the tasks CLI.\n"
    "- Do not stop early; the watcher will evaluate completion from task state after output settles.\n"
)


def load_sessions(path: Path) -> dict[str, AgentSession]:
    raw = load_json(path, default_factory=lambda: {"sessions": []})
    return _deserialize_sessions(raw)


def append_log_line(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{datetime.now(UTC).isoformat()}] {message}\n")


def _deserialize_sessions(raw: dict[str, object]) -> dict[str, AgentSession]:
    sessions: dict[str, AgentSession] = {}
    for item in raw.get("sessions", []):
        sessions[item["id"]] = AgentSession(
            id=item["id"],
            tmux_session=item["tmux_session"],
            role=item["role"],
            runtime=item["runtime"],
            workdir=Path(item["workdir"]),
            full_prompt=item["full_prompt"],
            status=item["status"],
            started_at=datetime.fromisoformat(item["started_at"]),
            terminal_outcome=item.get("terminal_outcome"),
            round_index=item.get("round_index"),
            task_id=item.get("task_id"),
            task_title=item.get("task_title"),
            task_description=item.get("task_description"),
            finished_at=(
                datetime.fromisoformat(item["finished_at"])
                if item.get("finished_at")
                else None
            ),
            failure_reason=item.get("failure_reason"),
            failure_source=item.get("failure_source"),
            last_observed_at=(
                datetime.fromisoformat(item["last_observed_at"])
                if item.get("last_observed_at")
                else None
            ),
            agent_log_file=(
                Path(item["agent_log_file"])
                if item.get("agent_log_file")
                else None
            ),
            worktree_path=Path(item["worktree_path"]) if item.get("worktree_path") else None,
            wiki_file_paths=tuple(item["wiki_file_paths"]) if item.get("wiki_file_paths") else None,
        )
    return sessions


class Orchestrator:
    def __init__(
        self,
        root: Path,
        *,
        roles_path: Path | None = None,
        tmux_client: TmuxClient | None = None,
        watcher_launcher=None,
        run_id: str | None = None,
    ) -> None:
        self.root = root.resolve()
        self.roles_path = roles_path or self.root / "src" / "roles"
        self.state_dir = self.root / ".babel-agent"
        self.config = BabelConfig.load(self.state_dir / "config.toml")
        self.run_id = run_id
        self.state_path = sessions_file_path(self.root, run_id)
        self.tmux = tmux_client or TmuxClient()
        self._state_lock = threading.RLock()
        self._test_write_delay = 0.0
        self._watcher_launcher = watcher_launcher or spawn_completion_watcher

    def session_log_path(self, session_id: str) -> Path:
        return session_log_path(self.root, session_id, self.run_id)

    def create_session(self, spec: AgentSessionSpec) -> AgentSession:
        role = self._load_role(spec.role)
        runtime = get_runtime(spec.runtime)
        override = self.config.get_role_override(spec.role)
        effective_model = override.model or role.model
        effective_thinking = override.thinking or role.thinking
        effective_command = list(runtime.startup_command)
        if effective_model:
            effective_command.extend(["-m", effective_model])
        if effective_thinking:
            effective_command.extend(["--thinking", effective_thinking])

        session_id = spec.session_id or self._new_session_id()
        tmux_session = spec.session_name or session_id
        role_prompt = render_role_prompt(role.prompt, spec.template_vars or {})
        full_prompt = build_full_prompt(role_prompt, spec.session_prompt)
        started_at = datetime.now(UTC)

        record = AgentSession(
            id=session_id,
            tmux_session=tmux_session,
            role=spec.role,
            runtime=spec.runtime,
            workdir=spec.workdir.resolve(),
            full_prompt=full_prompt,
            status="starting",
            started_at=started_at,
            round_index=spec.round_index,
            task_id=spec.task_id,
            task_title=spec.task_title,
            task_description=spec.task_description,
            last_observed_at=started_at,
            worktree_path=spec.worktree_path,
        )
        self._upsert(record)
        append_log_line(
            self.session_log_path(record.id),
            f"session created role={record.role} tmux_session={record.tmux_session} round={record.round_index} task_id={record.task_id}",
        )

        if shutil.which(effective_command[0]) is None:
            message = f"runtime binary not found: {runtime.startup_command[0]}"
            failed = self._with_status(
                record,
                "failed",
                terminal_outcome="failed",
                failure_reason=message,
                failure_source="launch",
            )
            self._upsert(failed)
            append_log_line(self.session_log_path(record.id), f"session launch failed: {message}")
            raise FileNotFoundError(message)

        _max_launch_attempts = 5
        try:
            append_log_line(
                self.session_log_path(record.id),
                f"launching tmux session={tmux_session} workdir={record.workdir}",
            )
            pre_launch_time = datetime.now(UTC)
            for attempt in range(1, _max_launch_attempts + 1):
                self.tmux.create_session(tmux_session, record.workdir, effective_command)
                try:
                    self.tmux.wait_until_ready(tmux_session, runtime.ready_strategy)
                    break
                except Exception:
                    self.tmux.kill_session(tmux_session)
                    if attempt == _max_launch_attempts:
                        raise
                    append_log_line(
                        self.session_log_path(record.id),
                        f"session not ready after attempt {attempt}/{_max_launch_attempts}; restarting",
                    )
            self.tmux.paste_and_submit(tmux_session, full_prompt)
            # agent_log_file will be back-filled by _start_log_file_scanner
        except Exception as exc:
            self.tmux.kill_session(tmux_session)
            failed = self._with_status(
                record,
                "failed",
                terminal_outcome="failed",
                failure_reason=str(exc) or exc.__class__.__name__,
                failure_source="launch",
            )
            self._upsert(failed)
            append_log_line(self.session_log_path(record.id), "session launch raised an exception")
            append_log_line(self.session_log_path(record.id), traceback.format_exc().rstrip())
            raise

        running = replace(self._with_status(record, "running"), agent_log_file=None)
        self._upsert(running)
        append_log_line(
            self.session_log_path(running.id),
            f"session running; watcher starting for tmux_session={running.tmux_session} agent_log_file={running.agent_log_file}",
        )
        self._watcher_launcher(self.root, self.run_id, running.id, running.tmux_session, runtime.ready_strategy.prompt_prefix)
        self._start_log_file_scanner(running.id, runtime.agent_logs_dir, pre_launch_time)
        return running

    def get_session(self, session_id: str) -> AgentSession | None:
        sessions = self._load_state()
        if session_id not in sessions:
            return None
        session = sessions[session_id]
        return self._reconcile_one(session)

    def list_sessions(self) -> list[AgentSession]:
        def reconcile(sessions: dict[str, AgentSession]) -> list[AgentSession]:
            reconciled = [self._reconcile_one(session, persist=False) for session in sessions.values()]
            sessions.clear()
            sessions.update({session.id: session for session in reconciled})
            return reconciled

        with self._state_lock:
            reconciled = self._mutate_state(reconcile)
        return sorted(reconciled, key=lambda item: item.started_at)

    def kill_session(self, session_id: str) -> None:
        session = self.get_session(session_id)
        if session is None:
            return
        self.tmux.kill_session(session.tmux_session)
        self._upsert(self._with_status(session, "stopped", terminal_outcome="abandoned"))
        append_log_line(self.session_log_path(session_id), f"session abandoned by orchestrator for tmux_session={session.tmux_session}")

    def _load_role(self, role_name: str):
        try:
            roles = load_roles(self.roles_path)
        except FileNotFoundError as exc:
            raise RoleRegistryError(f"roles path not found: {self.roles_path}") from exc
        try:
            return roles[role_name]
        except KeyError as exc:
            raise RoleRegistryError(f"unknown role: {role_name}") from exc

    def _new_session_id(self) -> str:
        return secrets.token_hex(4)

    def _reconcile_one(self, session: AgentSession, *, persist: bool = True) -> AgentSession:
        if session.status in {"running", "starting"} and not self.tmux.has_session(session.tmux_session):
            session = self._with_status(
                session,
                "stopped",
                terminal_outcome="failed",
                failure_reason=f"tmux session disappeared: {session.tmux_session}",
                failure_source="tmux_missing",
            )
            if persist:
                self._upsert(session)
                append_log_line(self.session_log_path(session.id), f"reconciled failed because tmux session disappeared: {session.tmux_session}")
        elif (
            session.status == "stopped"
            and session.terminal_outcome is None
            and not self.tmux.has_session(session.tmux_session)
        ):
            session = self._with_status(
                session,
                "stopped",
                terminal_outcome="failed",
                failure_reason=f"tmux session disappeared before terminal outcome: {session.tmux_session}",
                failure_source="tmux_missing",
            )
            if persist:
                self._upsert(session)
                append_log_line(
                    self.session_log_path(session.id),
                    f"reconciled stopped session without outcome because tmux session disappeared: {session.tmux_session}",
                )
        return session

    def _start_log_file_scanner(
        self,
        session_id: str,
        logs_dir: Path | None,
        after: datetime,
        timeout_seconds: float = 120.0,
    ) -> None:
        if logs_dir is None:
            return

        def _scan() -> None:
            found = _poll_for_agent_log_file(logs_dir, after, timeout_seconds=timeout_seconds)
            if found is None:
                append_log_line(
                    self.session_log_path(session_id),
                    f"log file scanner timed out after {timeout_seconds}s; agent_log_file remains null",
                )
                return

            def _apply(sessions: dict) -> None:
                session = sessions.get(session_id)
                if session is None:
                    return
                sessions[session_id] = replace(session, agent_log_file=found)

            with self._state_lock:
                self._mutate_state(_apply)
            append_log_line(
                self.session_log_path(session_id),
                f"log file scanner found agent_log_file={found}",
            )

        threading.Thread(target=_scan, daemon=True, name=f"log-scanner-{session_id}").start()

    def _upsert(self, session: AgentSession) -> None:
        with self._state_lock:
            self._mutate_state(lambda sessions: sessions.__setitem__(session.id, session))

    def _load_state(self) -> dict[str, AgentSession]:
        return load_sessions(self.state_path)

    def _write_state(self, sessions: dict[str, AgentSession]) -> None:
        payload = {
            "sessions": [
                {
                    **asdict(session),
                    "workdir": str(session.workdir),
                    "started_at": session.started_at.isoformat(),
                    "finished_at": session.finished_at.isoformat() if session.finished_at is not None else None,
                    "last_observed_at": (
                        session.last_observed_at.isoformat() if session.last_observed_at is not None else None
                    ),
                    "agent_log_file": str(session.agent_log_file) if session.agent_log_file is not None else None,
                    "worktree_path": str(session.worktree_path) if session.worktree_path is not None else None,
                }
                for session in sorted(sessions.values(), key=lambda item: item.started_at)
            ]
        }
        write_json_atomic(self.state_path, payload)

    def _mark_terminal_outcome_if_present(
        self,
        session_id: str,
        outcome: str,
        *,
        failure_reason: str | None = None,
        failure_source: str | None = None,
    ) -> None:
        with self._state_lock:
            self._mutate_state(
                finalize_outcome=outcome,
                session_id=session_id,
                failure_reason=failure_reason,
                failure_source=failure_source,
            )

    def _mutate_state(
        self,
        mutator=None,
        *,
        finalize_outcome: str | None = None,
        session_id: str | None = None,
        failure_reason: str | None = None,
        failure_source: str | None = None,
    ):
        with locked_json_store(self.state_path, default_factory=lambda: {"sessions": []}) as raw:
            sessions = _deserialize_sessions(raw)
            if finalize_outcome is not None:
                if session_id is None:
                    raise ValueError("session_id is required when finalize_outcome is set")
                session = sessions.get(session_id)
                if session is None:
                    return None
                sessions[session_id] = self._with_status(
                    session,
                    "stopped",
                    terminal_outcome=finalize_outcome,
                    failure_reason=failure_reason,
                    failure_source=failure_source,
                )
                result = None
            else:
                if mutator is None:
                    raise ValueError("mutator is required")
                result = mutator(sessions)
            if self._test_write_delay:
                time.sleep(self._test_write_delay)
            self._write_state(sessions)
            return result

    @staticmethod
    def _with_status(
        session: AgentSession,
        status: str,
        *,
        terminal_outcome: str | None = None,
        failure_reason: str | None = None,
        failure_source: str | None = None,
    ) -> AgentSession:
        next_terminal_outcome = session.terminal_outcome if terminal_outcome is None else terminal_outcome
        finished_at = session.finished_at
        if status == "stopped" and next_terminal_outcome is not None and finished_at is None:
            finished_at = datetime.now(UTC)
        next_failure_reason = session.failure_reason
        next_failure_source = session.failure_source
        if next_terminal_outcome == "completed":
            next_failure_reason = None
            next_failure_source = None
        else:
            if failure_reason is not None:
                next_failure_reason = failure_reason
            if failure_source is not None:
                next_failure_source = failure_source
        return AgentSession(
            id=session.id,
            tmux_session=session.tmux_session,
            role=session.role,
            runtime=session.runtime,
            workdir=session.workdir,
            full_prompt=session.full_prompt,
            status=status,
            started_at=session.started_at,
            terminal_outcome=next_terminal_outcome,
            round_index=session.round_index,
            task_id=session.task_id,
            task_title=session.task_title,
            task_description=session.task_description,
            finished_at=finished_at,
            failure_reason=next_failure_reason,
            failure_source=next_failure_source,
            last_observed_at=datetime.now(UTC),
            agent_log_file=session.agent_log_file,
            worktree_path=session.worktree_path,
            wiki_file_paths=session.wiki_file_paths,
        )


def _poll_for_agent_log_file(
    logs_dir: Path | None,
    after: datetime,
    timeout_seconds: float = 10.0,
    poll_interval_seconds: float = 0.5,
) -> Path | None:
    if logs_dir is None:
        return None
    d = after.astimezone().date()
    date_dir = logs_dir / f"{d.year:04d}" / f"{d.month:02d}" / f"{d.day:02d}"
    threshold = after.timestamp()
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if date_dir.is_dir():
            candidates = [p for p in date_dir.glob("*.jsonl") if p.stat().st_mtime >= threshold]
            if candidates:
                return max(candidates, key=lambda p: p.stat().st_mtime)
        time.sleep(poll_interval_seconds)
    return None


def build_full_prompt(role_prompt: str, session_prompt: str) -> str:
    return f"{role_prompt.strip()}\n\n{SESSION_MARKER_INSTRUCTIONS}\n{session_prompt.strip()}"


def render_role_prompt(role_prompt: str, template_vars: dict[str, object]) -> str:
    env = jinja2.Environment(keep_trailing_newline=True)
    return env.from_string(role_prompt).render(**template_vars)


def spawn_completion_watcher(root: Path, run_id: str | None, session_id: str, tmux_session: str, prompt_prefix: str) -> None:
    command = [
        sys.executable,
        "-m",
        "src.runtime.watcher",
        "--root",
        str(root),
        "--session-id",
        session_id,
        "--tmux-session",
        tmux_session,
        "--prompt-prefix",
        prompt_prefix,
    ]
    if run_id is not None:
        command.extend(["--run-id", run_id])
    log_path = session_log_path(root, session_id, run_id)
    append_log_line(log_path, f"spawning watcher for tmux_session={tmux_session}")
    handle = log_path.open("a", encoding="utf-8")
    subprocess.Popen(
        command,
        stdout=handle,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        cwd=str(root),
    )
    handle.close()


def watch_session_completion(
    root: Path,
    run_id: str | None,
    session_id: str,
    tmux_session: str,
    prompt_prefix: str,
    *,
    tmux_client: TmuxClient | None = None,
    poll_interval_seconds: float = STATE_POLL_INTERVAL_SECONDS,
) -> None:
    orchestrator = Orchestrator(root, tmux_client=tmux_client, watcher_launcher=lambda *_args: None, run_id=run_id)
    log_path = orchestrator.session_log_path(session_id)
    append_log_line(log_path, f"watcher started tmux_session={tmux_session} prompt_prefix={prompt_prefix!r}")
    outcome: str
    failure_reason: str | None
    failure_source: str | None
    try:
        while True:
            if not orchestrator.tmux.has_session(tmux_session):
                outcome = "failed"
                failure_reason = f"tmux session exited before task completed: {tmux_session}"
                failure_source = "tmux_missing"
                append_log_line(log_path, f"watcher observed tmux session exit: {tmux_session}")
                break

            session = orchestrator.get_session(session_id)
            if session is None:
                outcome = "failed"
                failure_reason = f"session missing during completion evaluation: {session_id}"
                failure_source = "watcher"
                append_log_line(log_path, f"watcher session missing: {session_id}")
                break

            candidate_outcome, candidate_reason, candidate_source = _evaluate_completion(root, run_id, session)
            if candidate_outcome in ("completed", "failed"):
                outcome = candidate_outcome
                failure_reason = candidate_reason
                failure_source = candidate_source
                append_log_line(log_path, f"watcher detected terminal state outcome={outcome} reason={failure_reason!r}")
                break

            time.sleep(poll_interval_seconds)

        orchestrator._mark_terminal_outcome_if_present(
            session_id,
            outcome,
            failure_reason=failure_reason,
            failure_source=failure_source,
        )
    except Exception:
        append_log_line(log_path, "watcher raised an exception")
        append_log_line(log_path, traceback.format_exc().rstrip())
        orchestrator._mark_terminal_outcome_if_present(
            session_id,
            "failed",
            failure_reason="watcher raised an exception",
            failure_source="watcher",
        )
        raise
    finally:
        append_log_line(log_path, f"watcher stopping tmux_session={tmux_session}")
        orchestrator.tmux.kill_session(tmux_session)
        session_record = orchestrator.get_session(session_id)
        if session_record is not None and session_record.worktree_path is not None:
            remove_worktree(orchestrator.root, session_id)


def _evaluate_completion(root: Path, run_id: str | None, session: AgentSession) -> tuple[str, str | None, str | None]:
    """Return (outcome, failure_reason, failure_source).

    outcome is one of:
    - "completed" — terminal state reached, session is done
    - "pending"   — not ready yet, caller should keep polling
    - "failed"    — hard error, stop immediately
    """
    if run_id is None:
        return "failed", "session completion requires run_id-bound task state", "watcher"

    store_path = tasks_file_path(root, run_id)
    if session.role == "planner":
        task_state = tasks("list", path=store_path, view="all", include_full=True)["result"]
        if task_state.get("status") == "ready":
            return "completed", None, None
        return "pending", None, None

    if session.role == "worker":
        if not session.task_id:
            return "failed", "worker session missing assigned task id", "watcher"
        try:
            task_item = tasks("get", path=store_path, id=session.task_id)["result"]
        except Exception as exc:
            return "failed", f"worker session task lookup failed: {exc}", "watcher"
        if task_item.get("status") == "closed":
            return "completed", None, None
        return "pending", None, None

    if session.role == "librarian":
        task_state = tasks("list", path=store_path, view="all", include_full=True)["result"]
        if task_state.get("status") == "wiki_ready":
            return "completed", None, None
        return "pending", None, None

    return "failed", f"unsupported role completion trigger: {session.role}", "watcher"
