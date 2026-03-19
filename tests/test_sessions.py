from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from dataclasses import replace

from src.orchestration.runs import RunStore
from src.orchestration.sessions import Orchestrator, render_role_prompt, watch_session_completion
from src.orchestration.state_paths import session_log_path, tasks_file_path
from src.runtime.tmux import TmuxClient
from src.tasks.store import tasks
from src.utils.models import AgentSessionSpec


class FakeTmuxClient:
    def __init__(self) -> None:
        self.sessions: set[str] = set()
        self.panes: dict[str, str] = {}
        self.on_kill_session = None
        self.last_command: list[str] | None = None

    def create_session(self, session_name: str, workdir: Path, command: list[str]) -> None:
        self.sessions.add(session_name)
        self.panes.setdefault(session_name, "")
        self.last_command = list(command)

    def wait_until_ready(self, session_name: str, strategy) -> None:
        return None

    def paste_and_submit(self, session_name: str, text: str) -> None:
        return None

    def has_session(self, session_name: str) -> bool:
        return session_name in self.sessions

    def kill_session(self, session_name: str) -> None:
        if self.on_kill_session is not None:
            self.on_kill_session(session_name)
        self.sessions.discard(session_name)

    def capture_pane(self, session_name: str, start: int = -100) -> str:
        return self.panes.get(session_name, "")

    def has_active_generation_marker(self, pane: str) -> bool:
        return False

    def detect_terminal_outcome(self, session_name: str, *, start: int = -40) -> str | None:
        pane = self.capture_pane(session_name, start=start)
        return TmuxClient.terminal_outcome_from_pane(pane)


class FakeWatcherLauncher:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, str | None, str, str, str]] = []

    def __call__(self, root: Path, run_id: str | None, session_id: str, tmux_session: str, prompt_prefix: str) -> None:
        self.calls.append((root, run_id, session_id, tmux_session, prompt_prefix))


class SessionsTest(unittest.TestCase):
    def test_render_role_prompt_substitutes_template_vars(self) -> None:
        prompt = render_role_prompt("Limit {{max_workers}} workers.", {"max_workers": 3})
        self.assertEqual(prompt, "Limit 3 workers.")

    def test_create_session_persists_and_reconciles(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            roles_path = root / "src" / "roles"
            roles_path.mkdir(parents=True)
            (roles_path / "planner.yaml").write_text(
                "prompt: |\n"
                "  Plan carefully.\n",
                encoding="utf-8",
            )
            fake_tmux = FakeTmuxClient()
            orchestrator = Orchestrator(root, tmux_client=fake_tmux, watcher_launcher=FakeWatcherLauncher())
            session = orchestrator.create_session(
                AgentSessionSpec(
                    role="planner",
                    runtime="codex",
                    session_prompt="Work on task X.",
                    workdir=root,
                )
            )

            self.assertEqual(session.status, "running")
            listed = orchestrator.list_sessions()
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0].tmux_session, session.tmux_session)

            fake_tmux.kill_session(session.tmux_session)
            reconciled = orchestrator.get_session(session.id)
            self.assertIsNotNone(reconciled)
            self.assertEqual(reconciled.status, "stopped")
            self.assertEqual(reconciled.terminal_outcome, "failed")
            self.assertEqual(reconciled.failure_source, "tmux_missing")
            self.assertIn("tmux session disappeared", reconciled.failure_reason)

    def test_create_session_starts_completion_watcher(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            roles_path = root / "src" / "roles"
            roles_path.mkdir(parents=True)
            (roles_path / "planner.yaml").write_text(
                "prompt: 'Plan carefully.'\n",
                encoding="utf-8",
            )
            fake_tmux = FakeTmuxClient()
            launcher = FakeWatcherLauncher()
            orchestrator = Orchestrator(root, tmux_client=fake_tmux, watcher_launcher=launcher)
            session = orchestrator.create_session(
                AgentSessionSpec(
                    role="planner",
                    runtime="codex",
                    session_prompt="Start.",
                    workdir=root,
                )
            )

            self.assertEqual(len(launcher.calls), 1)
            launch_call = launcher.calls[0]
            self.assertEqual(launch_call[0], root.resolve())
            self.assertIsNone(launch_call[1])
            self.assertEqual(launch_call[2], session.id)
            self.assertEqual(launch_call[3], session.tmux_session)
            self.assertEqual(launch_call[4], "› ")

    def test_create_session_persists_activity_metadata(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            roles_path = root / "src" / "roles"
            roles_path.mkdir(parents=True)
            (roles_path / "worker.yaml").write_text(
                "prompt: 'Do the work.'\n",
                encoding="utf-8",
            )
            fake_tmux = FakeTmuxClient()
            orchestrator = Orchestrator(root, tmux_client=fake_tmux, watcher_launcher=FakeWatcherLauncher())

            session = orchestrator.create_session(
                AgentSessionSpec(
                    role="worker",
                    runtime="codex",
                    session_prompt="Handle task.",
                    workdir=root,
                    round_index=3,
                    task_id="task-9",
                    task_title="Add activity feed",
                    task_description="Build the live session feed.",
                )
            )

            persisted = orchestrator.get_session(session.id)
            self.assertIsNotNone(persisted)
            assert persisted is not None
            self.assertEqual(persisted.round_index, 3)
            self.assertEqual(persisted.task_id, "task-9")
            self.assertEqual(persisted.task_title, "Add activity feed")
            self.assertEqual(persisted.task_description, "Build the live session feed.")

    def test_completion_watcher_marks_planner_completed_when_run_is_ready(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = RunStore(root).create_run(initial_prompt="Plan.", workdir=root)
            roles_path = root / "src" / "roles"
            roles_path.mkdir(parents=True)
            (roles_path / "planner.yaml").write_text(
                "prompt: 'Plan carefully.'\n",
                encoding="utf-8",
            )
            fake_tmux = FakeTmuxClient()
            orchestrator = Orchestrator(root, tmux_client=fake_tmux, watcher_launcher=FakeWatcherLauncher(), run_id=run.id)
            session = orchestrator.create_session(
                AgentSessionSpec(
                    role="planner",
                    runtime="codex",
                    session_prompt="Start.",
                    workdir=root,
                )
            )
            tasks("ready", path=tasks_file_path(root, run.id))

            watch_session_completion(root, run.id, session.id, session.tmux_session, "› ", tmux_client=fake_tmux)

            reconciled = orchestrator.get_session(session.id)
            self.assertIsNotNone(reconciled)
            self.assertEqual(reconciled.status, "stopped")
            self.assertEqual(reconciled.terminal_outcome, "completed")
            self.assertIsNotNone(reconciled.finished_at)
            self.assertFalse(fake_tmux.has_session(session.tmux_session))
            self.assertIsNone(reconciled.failure_reason)
            self.assertIsNone(reconciled.failure_source)

    def test_completion_watcher_persists_terminal_outcome_before_tmux_teardown(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = RunStore(root).create_run(initial_prompt="Plan.", workdir=root)
            roles_path = root / "src" / "roles"
            roles_path.mkdir(parents=True)
            (roles_path / "planner.yaml").write_text(
                "prompt: 'Plan carefully.'\n",
                encoding="utf-8",
            )
            fake_tmux = FakeTmuxClient()
            orchestrator = Orchestrator(root, tmux_client=fake_tmux, watcher_launcher=FakeWatcherLauncher(), run_id=run.id)
            session = orchestrator.create_session(
                AgentSessionSpec(
                    role="planner",
                    runtime="codex",
                    session_prompt="Start.",
                    workdir=root,
                )
            )
            tasks("ready", path=tasks_file_path(root, run.id))
            outcomes_seen_during_kill: list[str | None] = []

            def record_outcome_during_kill(_session_name: str) -> None:
                current = orchestrator.get_session(session.id)
                outcomes_seen_during_kill.append(None if current is None else current.terminal_outcome)

            fake_tmux.on_kill_session = record_outcome_during_kill

            watch_session_completion(root, run.id, session.id, session.tmux_session, "› ", tmux_client=fake_tmux)

            self.assertEqual(outcomes_seen_during_kill, ["completed"])
            reconciled = orchestrator.get_session(session.id)
            self.assertIsNotNone(reconciled)
            self.assertEqual(reconciled.status, "stopped")
            self.assertEqual(reconciled.terminal_outcome, "completed")

    def test_completion_watcher_marks_planner_failed_when_tmux_exits_before_run_ready(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = RunStore(root).create_run(initial_prompt="Plan.", workdir=root)
            roles_path = root / "src" / "roles"
            roles_path.mkdir(parents=True)
            (roles_path / "planner.yaml").write_text(
                "prompt: 'Plan carefully.'\n",
                encoding="utf-8",
            )
            fake_tmux = FakeTmuxClient()
            orchestrator = Orchestrator(root, tmux_client=fake_tmux, watcher_launcher=FakeWatcherLauncher(), run_id=run.id)
            session = orchestrator.create_session(
                AgentSessionSpec(
                    role="planner",
                    runtime="codex",
                    session_prompt="Start.",
                    workdir=root,
                )
            )
            # tmux exits before the planner marks the run ready
            fake_tmux.kill_session(session.tmux_session)

            watch_session_completion(root, run.id, session.id, session.tmux_session, "› ", tmux_client=fake_tmux)

            reconciled = orchestrator.get_session(session.id)
            self.assertIsNotNone(reconciled)
            self.assertEqual(reconciled.status, "stopped")
            self.assertEqual(reconciled.terminal_outcome, "failed")
            self.assertFalse(fake_tmux.has_session(session.tmux_session))
            self.assertEqual(reconciled.failure_source, "tmux_missing")

    def test_completion_watcher_marks_worker_completed_when_task_is_closed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = RunStore(root).create_run(initial_prompt="Plan.", workdir=root)
            roles_path = root / "src" / "roles"
            roles_path.mkdir(parents=True)
            (roles_path / "worker.yaml").write_text("prompt: 'Do the work.'\n", encoding="utf-8")
            fake_tmux = FakeTmuxClient()
            task_id = tasks("create", path=tasks_file_path(root, run.id), title="T1", description="D1")["result"]["task"]["id"]
            tasks("assign", path=tasks_file_path(root, run.id), id=task_id)
            orchestrator = Orchestrator(root, tmux_client=fake_tmux, watcher_launcher=FakeWatcherLauncher(), run_id=run.id)
            session = orchestrator.create_session(
                AgentSessionSpec(
                    role="worker",
                    runtime="codex",
                    session_prompt="Start.",
                    workdir=root,
                    task_id=task_id,
                )
            )
            tasks("close", path=tasks_file_path(root, run.id), id=task_id, reason="done")

            watch_session_completion(root, run.id, session.id, session.tmux_session, "› ", tmux_client=fake_tmux)

            reconciled = orchestrator.get_session(session.id)
            self.assertIsNotNone(reconciled)
            self.assertEqual(reconciled.status, "stopped")
            self.assertEqual(reconciled.terminal_outcome, "completed")
            self.assertFalse(fake_tmux.has_session(session.tmux_session))
            self.assertIsNone(reconciled.failure_source)

    def test_completion_watcher_marks_worker_failed_when_tmux_exits_before_task_closed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = RunStore(root).create_run(initial_prompt="Plan.", workdir=root)
            roles_path = root / "src" / "roles"
            roles_path.mkdir(parents=True)
            (roles_path / "worker.yaml").write_text("prompt: 'Do the work.'\n", encoding="utf-8")
            fake_tmux = FakeTmuxClient()
            task_id = tasks("create", path=tasks_file_path(root, run.id), title="T1", description="D1")["result"]["task"]["id"]
            tasks("assign", path=tasks_file_path(root, run.id), id=task_id)
            orchestrator = Orchestrator(root, tmux_client=fake_tmux, watcher_launcher=FakeWatcherLauncher(), run_id=run.id)
            session = orchestrator.create_session(
                AgentSessionSpec(
                    role="worker",
                    runtime="codex",
                    session_prompt="Start.",
                    workdir=root,
                    task_id=task_id,
                )
            )
            # tmux exits before the worker closes its task
            fake_tmux.kill_session(session.tmux_session)

            watch_session_completion(root, run.id, session.id, session.tmux_session, "› ", tmux_client=fake_tmux)

            reconciled = orchestrator.get_session(session.id)
            self.assertIsNotNone(reconciled)
            self.assertEqual(reconciled.status, "stopped")
            self.assertEqual(reconciled.terminal_outcome, "failed")
            self.assertEqual(reconciled.failure_source, "tmux_missing")
            self.assertFalse(fake_tmux.has_session(session.tmux_session))

    def test_completion_watcher_marks_session_failed_when_tmux_exits_early(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            roles_path = root / "src" / "roles"
            roles_path.mkdir(parents=True)
            (roles_path / "planner.yaml").write_text(
                "prompt: 'Plan carefully.'\n",
                encoding="utf-8",
            )
            fake_tmux = FakeTmuxClient()
            orchestrator = Orchestrator(root, tmux_client=fake_tmux, watcher_launcher=FakeWatcherLauncher())
            session = orchestrator.create_session(
                AgentSessionSpec(
                    role="planner",
                    runtime="codex",
                    session_prompt="Start.",
                    workdir=root,
                )
            )
            fake_tmux.kill_session(session.tmux_session)

            watch_session_completion(root, None, session.id, session.tmux_session, "› ", tmux_client=fake_tmux)

            reconciled = orchestrator.get_session(session.id)
            self.assertIsNotNone(reconciled)
            self.assertEqual(reconciled.status, "stopped")
            self.assertEqual(reconciled.terminal_outcome, "failed")

    def test_kill_session_marks_abandoned(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            roles_path = root / "src" / "roles"
            roles_path.mkdir(parents=True)
            (roles_path / "planner.yaml").write_text(
                "prompt: 'Plan carefully.'\n",
                encoding="utf-8",
            )
            fake_tmux = FakeTmuxClient()
            orchestrator = Orchestrator(root, tmux_client=fake_tmux, watcher_launcher=FakeWatcherLauncher())
            session = orchestrator.create_session(
                AgentSessionSpec(
                    role="planner",
                    runtime="codex",
                    session_prompt="Start.",
                    workdir=root,
                )
            )

            orchestrator.kill_session(session.id)

            reconciled = orchestrator.get_session(session.id)
            self.assertIsNotNone(reconciled)
            self.assertEqual(reconciled.status, "stopped")
            self.assertEqual(reconciled.terminal_outcome, "abandoned")

    def test_reconcile_marks_stopped_session_without_terminal_outcome_as_failed_once_tmux_is_gone(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            roles_path = root / "src" / "roles"
            roles_path.mkdir(parents=True)
            (roles_path / "planner.yaml").write_text(
                "prompt: 'Plan carefully.'\n",
                encoding="utf-8",
            )
            fake_tmux = FakeTmuxClient()
            orchestrator = Orchestrator(root, tmux_client=fake_tmux, watcher_launcher=FakeWatcherLauncher())
            session = orchestrator.create_session(
                AgentSessionSpec(
                    role="planner",
                    runtime="codex",
                    session_prompt="Start.",
                    workdir=root,
                )
            )
            stale = replace(session, status="stopped", terminal_outcome=None)
            orchestrator._upsert(stale)
            fake_tmux.kill_session(session.tmux_session)

            reconciled = orchestrator.get_session(session.id)

            self.assertIsNotNone(reconciled)
            self.assertEqual(reconciled.status, "stopped")
            self.assertEqual(reconciled.terminal_outcome, "failed")
            self.assertEqual(reconciled.failure_source, "tmux_missing")
            self.assertIn("before terminal outcome", reconciled.failure_reason)

    def test_default_session_name_uses_role_and_shortid(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            roles_path = root / "src" / "roles"
            roles_path.mkdir(parents=True)
            (roles_path / "planner.yaml").write_text(
                "prompt: 'Plan carefully.'\n",
                encoding="utf-8",
            )
            fake_tmux = FakeTmuxClient()
            orchestrator = Orchestrator(root, tmux_client=fake_tmux, watcher_launcher=FakeWatcherLauncher())
            session = orchestrator.create_session(
                AgentSessionSpec(
                    role="planner",
                    runtime="codex",
                    session_prompt="Start.",
                    workdir=root,
                )
            )

            self.assertRegex(session.tmux_session, r"^[0-9a-f]{8}$")

    def test_concurrent_session_updates_do_not_clobber_other_records(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            roles_path = root / "src" / "roles"
            roles_path.mkdir(parents=True)
            (roles_path / "planner.yaml").write_text(
                "prompt: 'Plan carefully.'\n",
                encoding="utf-8",
            )
            first_tmux = FakeTmuxClient()
            second_tmux = FakeTmuxClient()
            first = Orchestrator(root, tmux_client=first_tmux, watcher_launcher=FakeWatcherLauncher())
            second = Orchestrator(root, tmux_client=second_tmux, watcher_launcher=FakeWatcherLauncher())
            first._test_write_delay = 0.2

            session_one = first.create_session(
                AgentSessionSpec(
                    role="planner",
                    runtime="codex",
                    session_prompt="First.",
                    workdir=root,
                    session_name="orch-planner-one",
                )
            )
            session_two = second.create_session(
                AgentSessionSpec(
                    role="planner",
                    runtime="codex",
                    session_prompt="Second.",
                    workdir=root,
                    session_name="orch-planner-two",
                )
            )

            def stop_first() -> None:
                first._upsert(replace(session_one, status="stopped"))

            def stop_second() -> None:
                second._upsert(replace(session_two, status="stopped"))

            thread_one = threading.Thread(target=stop_first)
            thread_two = threading.Thread(target=stop_second)
            thread_one.start()
            time.sleep(0.05)
            thread_two.start()
            thread_one.join()
            thread_two.join()

            listed = {session.id: session for session in second.list_sessions()}
            self.assertEqual(set(listed), {session_one.id, session_two.id})
            self.assertEqual(listed[session_one.id].status, "stopped")
            self.assertEqual(listed[session_two.id].status, "stopped")

    def test_session_log_is_written_for_watcher_lifecycle(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = RunStore(root).create_run(initial_prompt="Plan.", workdir=root)
            roles_path = root / "src" / "roles"
            roles_path.mkdir(parents=True)
            (roles_path / "planner.yaml").write_text(
                "prompt: 'Plan carefully.'\n",
                encoding="utf-8",
            )
            fake_tmux = FakeTmuxClient()
            orchestrator = Orchestrator(root, tmux_client=fake_tmux, watcher_launcher=FakeWatcherLauncher(), run_id=run.id)
            session = orchestrator.create_session(
                AgentSessionSpec(
                    role="planner",
                    runtime="codex",
                    session_prompt="Start.",
                    workdir=root,
                )
            )
            tasks("ready", path=tasks_file_path(root, run.id))

            watch_session_completion(root, run.id, session.id, session.tmux_session, "› ", tmux_client=fake_tmux)

            log_text = session_log_path(root, session.id, run.id).read_text(encoding="utf-8")
            self.assertIn("session created", log_text)
            self.assertIn("watcher started", log_text)
            self.assertIn("watcher detected terminal state outcome=completed", log_text)

    def test_worktree_path_round_trip_serialization(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            roles_path = root / "src" / "roles"
            roles_path.mkdir(parents=True)
            (roles_path / "worker.yaml").write_text(
                "prompt: 'Do the work.'\n",
                encoding="utf-8",
            )
            fake_tmux = FakeTmuxClient()
            orchestrator = Orchestrator(root, tmux_client=fake_tmux, watcher_launcher=FakeWatcherLauncher())
            worktree = root / ".worktrees" / "abc123"
            session = orchestrator.create_session(
                AgentSessionSpec(
                    role="worker",
                    runtime="codex",
                    session_prompt="Do work.",
                    workdir=root,
                    worktree_path=worktree,
                )
            )

            persisted = orchestrator.get_session(session.id)
            self.assertIsNotNone(persisted)
            assert persisted is not None
            self.assertEqual(persisted.worktree_path, worktree)

    def test_worktree_path_none_round_trips(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            roles_path = root / "src" / "roles"
            roles_path.mkdir(parents=True)
            (roles_path / "worker.yaml").write_text(
                "prompt: 'Do the work.'\n",
                encoding="utf-8",
            )
            fake_tmux = FakeTmuxClient()
            orchestrator = Orchestrator(root, tmux_client=fake_tmux, watcher_launcher=FakeWatcherLauncher())
            session = orchestrator.create_session(
                AgentSessionSpec(
                    role="worker",
                    runtime="codex",
                    session_prompt="Do work.",
                    workdir=root,
                )
            )

            persisted = orchestrator.get_session(session.id)
            self.assertIsNotNone(persisted)
            assert persisted is not None
            self.assertIsNone(persisted.worktree_path)

    def test_inactivity_timeout_worker_closes_task_and_completes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = RunStore(root).create_run(initial_prompt="Plan.", workdir=root)
            roles_path = root / "src" / "roles"
            roles_path.mkdir(parents=True)
            (roles_path / "worker.yaml").write_text("prompt: 'Do the work.'\n", encoding="utf-8")
            fake_tmux = FakeTmuxClient()
            task_id = tasks("create", path=tasks_file_path(root, run.id), title="T1", description="D1")["result"]["task"]["id"]
            tasks("assign", path=tasks_file_path(root, run.id), id=task_id)
            orchestrator = Orchestrator(root, tmux_client=fake_tmux, watcher_launcher=FakeWatcherLauncher(), run_id=run.id)
            session = orchestrator.create_session(
                AgentSessionSpec(
                    role="worker",
                    runtime="codex",
                    session_prompt="Start.",
                    workdir=root,
                    task_id=task_id,
                )
            )
            # Pane stays at a fixed non-empty value — simulates a frozen agent
            fake_tmux.panes[session.tmux_session] = "some static output"

            watch_session_completion(
                root, run.id, session.id, session.tmux_session, "› ",
                tmux_client=fake_tmux,
                inactivity_timeout_seconds=0.05,
                poll_interval_seconds=0.01,
            )

            reconciled = orchestrator.get_session(session.id)
            self.assertIsNotNone(reconciled)
            assert reconciled is not None
            self.assertEqual(reconciled.terminal_outcome, "completed")
            self.assertIsNone(reconciled.failure_source)
            # Task should be closed with a TIMEOUT reason
            closed = tasks("get", path=tasks_file_path(root, run.id), id=task_id)["result"]
            self.assertEqual(closed["status"], "closed")
            self.assertIn("TIMEOUT", closed["close_reason"])
            self.assertIn("inactive", closed["close_reason"])

    def test_inactivity_timeout_planner_marks_failed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = RunStore(root).create_run(initial_prompt="Plan.", workdir=root)
            roles_path = root / "src" / "roles"
            roles_path.mkdir(parents=True)
            (roles_path / "planner.yaml").write_text("prompt: 'Plan carefully.'\n", encoding="utf-8")
            fake_tmux = FakeTmuxClient()
            orchestrator = Orchestrator(root, tmux_client=fake_tmux, watcher_launcher=FakeWatcherLauncher(), run_id=run.id)
            session = orchestrator.create_session(
                AgentSessionSpec(
                    role="planner",
                    runtime="codex",
                    session_prompt="Start.",
                    workdir=root,
                )
            )
            # Pane stays at a fixed value — simulates a frozen planner
            fake_tmux.panes[session.tmux_session] = "some static output"

            watch_session_completion(
                root, run.id, session.id, session.tmux_session, "› ",
                tmux_client=fake_tmux,
                inactivity_timeout_seconds=0.05,
                poll_interval_seconds=0.01,
            )

            reconciled = orchestrator.get_session(session.id)
            self.assertIsNotNone(reconciled)
            assert reconciled is not None
            self.assertEqual(reconciled.terminal_outcome, "failed")
            self.assertEqual(reconciled.failure_source, "watcher_inactivity")
            self.assertIn("inactive", reconciled.failure_reason)

    def test_create_session_uses_spec_session_id(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            roles_path = root / "src" / "roles"
            roles_path.mkdir(parents=True)
            (roles_path / "worker.yaml").write_text(
                "prompt: 'Do the work.'\n",
                encoding="utf-8",
            )
            fake_tmux = FakeTmuxClient()
            orchestrator = Orchestrator(root, tmux_client=fake_tmux, watcher_launcher=FakeWatcherLauncher())
            session = orchestrator.create_session(
                AgentSessionSpec(
                    role="worker",
                    runtime="codex",
                    session_prompt="Do work.",
                    workdir=root,
                    session_id="deadbeef",
                )
            )

            self.assertEqual(session.id, "deadbeef")


class ConfigOverrideTest(unittest.TestCase):
    def _make_root(self, tmp: str, role_yaml: str = "prompt: 'Plan carefully.'\n", role: str = "planner") -> Path:
        root = Path(tmp)
        roles_path = root / "src" / "roles"
        roles_path.mkdir(parents=True)
        (roles_path / f"{role}.yaml").write_text(role_yaml, encoding="utf-8")
        return root

    def _create_session(self, root: Path, fake_tmux: FakeTmuxClient, role: str = "planner") -> None:
        orchestrator = Orchestrator(root, tmux_client=fake_tmux, watcher_launcher=FakeWatcherLauncher())
        orchestrator.create_session(
            AgentSessionSpec(
                role=role,
                runtime="codex",
                session_prompt="Start.",
                workdir=root,
            )
        )

    def test_no_config_no_extra_flags(self) -> None:
        with TemporaryDirectory() as tmp:
            root = self._make_root(tmp)
            fake_tmux = FakeTmuxClient()
            self._create_session(root, fake_tmux)
            assert fake_tmux.last_command is not None
            self.assertNotIn("-m", fake_tmux.last_command)
            self.assertNotIn("--thinking", fake_tmux.last_command)

    def test_config_model_appended(self) -> None:
        with TemporaryDirectory() as tmp:
            root = self._make_root(tmp)
            config_dir = root / ".babel-agent"
            config_dir.mkdir(parents=True)
            (config_dir / "config.toml").write_text(
                '[roles.planner]\nmodel = "o3"\n', encoding="utf-8"
            )
            fake_tmux = FakeTmuxClient()
            self._create_session(root, fake_tmux)
            assert fake_tmux.last_command is not None
            self.assertIn("-m", fake_tmux.last_command)
            idx = fake_tmux.last_command.index("-m")
            self.assertEqual(fake_tmux.last_command[idx + 1], "o3")
            self.assertNotIn("--thinking", fake_tmux.last_command)

    def test_config_overrides_yaml_model(self) -> None:
        with TemporaryDirectory() as tmp:
            root = self._make_root(tmp, role_yaml="prompt: 'Plan carefully.'\nmodel: o1\n")
            config_dir = root / ".babel-agent"
            config_dir.mkdir(parents=True)
            (config_dir / "config.toml").write_text(
                '[roles.planner]\nmodel = "o3"\n', encoding="utf-8"
            )
            fake_tmux = FakeTmuxClient()
            self._create_session(root, fake_tmux)
            assert fake_tmux.last_command is not None
            idx = fake_tmux.last_command.index("-m")
            self.assertEqual(fake_tmux.last_command[idx + 1], "o3")

    def test_config_model_yaml_thinking_both_appended(self) -> None:
        with TemporaryDirectory() as tmp:
            root = self._make_root(tmp, role_yaml="prompt: 'Plan carefully.'\nthinking: high\n")
            config_dir = root / ".babel-agent"
            config_dir.mkdir(parents=True)
            (config_dir / "config.toml").write_text(
                '[roles.planner]\nmodel = "o3"\n', encoding="utf-8"
            )
            fake_tmux = FakeTmuxClient()
            self._create_session(root, fake_tmux)
            assert fake_tmux.last_command is not None
            self.assertIn("-m", fake_tmux.last_command)
            self.assertIn("--thinking", fake_tmux.last_command)
            idx_m = fake_tmux.last_command.index("-m")
            self.assertEqual(fake_tmux.last_command[idx_m + 1], "o3")
            idx_t = fake_tmux.last_command.index("--thinking")
            self.assertEqual(fake_tmux.last_command[idx_t + 1], "high")


if __name__ == "__main__":
    unittest.main()
