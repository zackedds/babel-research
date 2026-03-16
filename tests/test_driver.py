from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.orchestration.driver import build_planner_prompt, build_worker_prompt, run_orchestration_loop, spawn_orchestration_driver
from src.orchestration.runs import RunStore
from src.orchestration.state_paths import driver_log_path, tasks_file_path
from src.tasks.store import tasks
from src.utils.models import AgentSession, AgentSessionSpec


class FakeOrchestrator:
    def __init__(self, root: Path, *, run_id: str | None = None) -> None:
        self.root = root
        self.run_id = run_id
        self.created_specs: list[AgentSessionSpec] = []
        self.sessions: dict[str, AgentSession] = {}
        self.next_id = 1
        self.default_terminal_outcome = "completed"
        self.outcomes_by_role: dict[str, list[str | None]] = {}

    def create_session(self, spec: AgentSessionSpec) -> AgentSession:
        session_id = f"session-{self.next_id}"
        self.next_id += 1
        terminal_outcomes = self.outcomes_by_role.get(spec.role, [])
        terminal_outcome = (
            terminal_outcomes.pop(0)
            if terminal_outcomes
            else self.default_terminal_outcome
        )
        if terminal_outcome == "completed" and self.run_id is not None:
            run_tasks_path = tasks_file_path(self.root, self.run_id)
            if spec.role == "planner":
                tasks("ready", path=run_tasks_path)
            if spec.role == "worker" and spec.task_id is not None:
                tasks("close", path=run_tasks_path, id=spec.task_id, reason="done")
        session = AgentSession(
            id=session_id,
            tmux_session=f"tmux-{session_id}",
            role=spec.role,
            runtime=spec.runtime,
            workdir=spec.workdir,
            full_prompt=spec.session_prompt,
            status="stopped",
            started_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
            terminal_outcome=terminal_outcome,
        )
        self.created_specs.append(spec)
        self.sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> AgentSession | None:
        return self.sessions.get(session_id)


class DriverTest(unittest.TestCase):
    def test_spawn_orchestration_driver_writes_to_run_log(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("src.orchestration.driver.subprocess.Popen") as popen:
                spawn_orchestration_driver(root, "run-1")

            self.assertTrue(driver_log_path(root, "run-1").exists())
            popen.assert_called_once()
            self.assertIn("spawning orchestration driver", driver_log_path(root, "run-1").read_text(encoding="utf-8"))

    def test_build_worker_prompt_includes_title_description_and_notes(self) -> None:
        prompt = build_worker_prompt(
            {
                "id": "task-3",
                "title": "Add parser",
                "description": "Implement CLI parsing",
                "notes": [{"text": "Keep behavior stable"}, {"text": "Add tests"}],
            },
            "run-123",
        )
        self.assertEqual(
            prompt,
            "Task ID: task-3\nRun ID: run-123\n\nAdd parser\n\nImplement CLI parsing\n\nTask commands:\n- Read: tasks --run-id run-123 get --id task-3\n- Note: tasks --run-id run-123 note-append --id task-3 --note \"<progress>\"\n- Close: tasks --run-id run-123 close --id task-3 --reason \"<result>\"\n\nWorker completion checklist:\n1. Read the assignment and implement the requested work.\n2. Verify the behavior you changed.\n3. Final required command for success: tasks --run-id run-123 close --id task-3 --reason \"<result>\"\n4. Do not stop before step 3 has succeeded.\n\nNotes:\n- Keep behavior stable\n- Add tests",
        )

    def test_build_worker_prompt_renders_none_for_empty_notes(self) -> None:
        prompt = build_worker_prompt({"id": "task-1", "title": "T", "description": "D", "notes": []}, "run-123")
        self.assertEqual(
            prompt,
            "Task ID: task-1\nRun ID: run-123\n\nT\n\nD\n\nTask commands:\n- Read: tasks --run-id run-123 get --id task-1\n- Note: tasks --run-id run-123 note-append --id task-1 --note \"<progress>\"\n- Close: tasks --run-id run-123 close --id task-1 --reason \"<result>\"\n\nWorker completion checklist:\n1. Read the assignment and implement the requested work.\n2. Verify the behavior you changed.\n3. Final required command for success: tasks --run-id run-123 close --id task-1 --reason \"<result>\"\n4. Do not stop before step 3 has succeeded.\n\nNotes:\n- none",
        )

    def test_build_planner_prompt_includes_ready_command(self) -> None:
        prompt = build_planner_prompt("Plan work.", "run-123")
        self.assertIn("Run ID: run-123", prompt)
        self.assertIn("tasks --run-id run-123 ready", prompt)
        self.assertIn("Final required command for success", prompt)

    def test_run_completes_when_initial_planner_produces_no_ready_tasks(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = RunStore(root)
            run = store.create_run(initial_prompt="Plan.", workdir=root)
            orchestrator = FakeOrchestrator(root, run_id=run.id)
            run_tasks_path = tasks_file_path(root, run.id)

            run_orchestration_loop(root, run.id, orchestrator=orchestrator, run_store=store, poll_interval_seconds=0)

            loaded = store.get_run(run.id)
            assert loaded is not None
            self.assertEqual(loaded.status, "completed")
            self.assertEqual(len(loaded.planner_session_ids), 1)
            self.assertEqual(loaded.worker_waves, [])
            self.assertEqual(orchestrator.created_specs[0].role, "planner")

    def test_run_launches_workers_then_next_planner(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = RunStore(root)
            run = store.create_run(initial_prompt="Plan.", workdir=root)
            orchestrator = FakeOrchestrator(root, run_id=run.id)
            run_tasks_path = tasks_file_path(root, run.id)
            tasks("create", path=run_tasks_path, title="T1", description="D1")
            tasks("create", path=run_tasks_path, title="T2", description="D2")

            run_orchestration_loop(root, run.id, orchestrator=orchestrator, run_store=store, poll_interval_seconds=0)

            loaded = store.get_run(run.id)
            assert loaded is not None
            self.assertEqual(loaded.status, "completed")
            self.assertEqual(len(loaded.planner_session_ids), 2)
            self.assertEqual(len(loaded.worker_waves), 1)
            self.assertEqual(len(loaded.worker_waves[0]), 2)
            self.assertEqual(loaded.rounds_completed, 1)
            self.assertEqual([spec.role for spec in orchestrator.created_specs], ["planner", "worker", "worker", "planner"])
            self.assertEqual(orchestrator.created_specs[0].round_index, 1)
            self.assertEqual(orchestrator.created_specs[1].round_index, 1)
            self.assertEqual(orchestrator.created_specs[1].task_id, "task-1")
            self.assertEqual(orchestrator.created_specs[1].task_title, "T1")
            self.assertEqual(orchestrator.created_specs[1].task_description, "D1")
            self.assertEqual(orchestrator.created_specs[2].task_id, "task-2")
            self.assertEqual(orchestrator.created_specs[3].round_index, 2)
            closed_view = tasks("list", path=run_tasks_path, view="closed", include_full=True)
            self.assertEqual(sorted(task["id"] for task in closed_view["result"]["tasks"]), ["task-1", "task-2"])
            self.assertEqual(orchestrator.created_specs[0].template_vars, {"max_workers": "unlimited"})

    def test_run_does_not_schedule_duplicate_wave_for_assigned_open_task(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = RunStore(root)
            run = store.create_run(initial_prompt="Plan.", workdir=root)
            orchestrator = FakeOrchestrator(root, run_id=run.id)
            run_tasks_path = tasks_file_path(root, run.id)
            tasks("create", path=run_tasks_path, title="T1", description="D1")

            run_orchestration_loop(root, run.id, orchestrator=orchestrator, run_store=store, poll_interval_seconds=0)

            loaded = store.get_run(run.id)
            assert loaded is not None
            self.assertEqual(loaded.status, "completed")
            self.assertEqual(len(loaded.worker_waves), 1)
            self.assertEqual(len(loaded.worker_waves[0]), 1)
            self.assertEqual([spec.role for spec in orchestrator.created_specs], ["planner", "worker", "planner"])
            self.assertEqual(loaded.claimed_task_ids, ["task-1"])
            task_item = tasks("get", path=run_tasks_path, id="task-1")["result"]
            self.assertEqual(task_item["status"], "closed")

    def test_run_respects_max_workers_per_wave(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = RunStore(root)
            run = store.create_run(initial_prompt="Plan.", workdir=root, max_workers=2, max_rounds=1)
            orchestrator = FakeOrchestrator(root, run_id=run.id)
            run_tasks_path = tasks_file_path(root, run.id)
            tasks("create", path=run_tasks_path, title="T1", description="D1")
            tasks("create", path=run_tasks_path, title="T2", description="D2")
            tasks("create", path=run_tasks_path, title="T3", description="D3")

            run_orchestration_loop(root, run.id, orchestrator=orchestrator, run_store=store, poll_interval_seconds=0)

            loaded = store.get_run(run.id)
            assert loaded is not None
            self.assertEqual(loaded.status, "completed")
            self.assertEqual(len(loaded.worker_waves), 1)
            self.assertEqual(len(loaded.worker_waves[0]), 2)
            self.assertEqual(loaded.rounds_completed, 1)
            self.assertEqual(loaded.claimed_task_ids, ["task-1", "task-2"])
            self.assertEqual(orchestrator.created_specs[0].template_vars, {"max_workers": 2})

    def test_run_respects_max_rounds(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = RunStore(root)
            run = store.create_run(initial_prompt="Plan.", workdir=root, max_rounds=1)
            orchestrator = FakeOrchestrator(root, run_id=run.id)
            tasks("create", path=tasks_file_path(root, run.id), title="T1", description="D1")

            run_orchestration_loop(root, run.id, orchestrator=orchestrator, run_store=store, poll_interval_seconds=0)

            loaded = store.get_run(run.id)
            assert loaded is not None
            self.assertEqual(loaded.status, "completed")
            self.assertEqual(loaded.current_phase, "stopped")
            self.assertEqual(loaded.rounds_completed, 1)
            self.assertEqual([spec.role for spec in orchestrator.created_specs], ["planner", "worker"])

    def test_run_fails_when_worker_stops_without_completion(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = RunStore(root)
            run = store.create_run(initial_prompt="Plan.", workdir=root)
            orchestrator = FakeOrchestrator(root, run_id=run.id)
            orchestrator.outcomes_by_role["worker"] = ["failed"]
            tasks("create", path=tasks_file_path(root, run.id), title="T1", description="D1")

            run_orchestration_loop(root, run.id, orchestrator=orchestrator, run_store=store, poll_interval_seconds=0)

            loaded = store.get_run(run.id)
            assert loaded is not None
            self.assertEqual(loaded.status, "failed")
            self.assertEqual(loaded.current_phase, "stopped")
            self.assertEqual([spec.role for spec in orchestrator.created_specs], ["planner", "worker"])

    def test_run_fails_when_worker_stops_without_explicit_completed_outcome(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = RunStore(root)
            run = store.create_run(initial_prompt="Plan.", workdir=root)
            orchestrator = FakeOrchestrator(root, run_id=run.id)
            orchestrator.outcomes_by_role["worker"] = [None]
            tasks("create", path=tasks_file_path(root, run.id), title="T1", description="D1")

            run_orchestration_loop(root, run.id, orchestrator=orchestrator, run_store=store, poll_interval_seconds=0)

            loaded = store.get_run(run.id)
            assert loaded is not None
            self.assertEqual(loaded.status, "failed")
            self.assertEqual(loaded.current_phase, "stopped")
            self.assertEqual([spec.role for spec in orchestrator.created_specs], ["planner", "worker"])

    def test_run_fails_when_planner_stops_without_completion(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = RunStore(root)
            run = store.create_run(initial_prompt="Plan.", workdir=root)
            orchestrator = FakeOrchestrator(root, run_id=run.id)
            orchestrator.outcomes_by_role["planner"] = ["abandoned"]

            run_orchestration_loop(root, run.id, orchestrator=orchestrator, run_store=store, poll_interval_seconds=0)

            loaded = store.get_run(run.id)
            assert loaded is not None
            self.assertEqual(loaded.status, "failed")
            self.assertEqual(loaded.current_phase, "stopped")
            self.assertEqual([spec.role for spec in orchestrator.created_specs], ["planner"])

    def test_run_fails_when_planner_stops_without_explicit_completed_outcome(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = RunStore(root)
            run = store.create_run(initial_prompt="Plan.", workdir=root)
            orchestrator = FakeOrchestrator(root, run_id=run.id)
            orchestrator.outcomes_by_role["planner"] = [None]

            run_orchestration_loop(root, run.id, orchestrator=orchestrator, run_store=store, poll_interval_seconds=0)

            loaded = store.get_run(run.id)
            assert loaded is not None
            self.assertEqual(loaded.status, "failed")
            self.assertEqual(loaded.current_phase, "stopped")
            self.assertEqual([spec.role for spec in orchestrator.created_specs], ["planner"])


    def test_build_worker_prompt_includes_branch_context(self) -> None:
        from pathlib import Path
        prompt = build_worker_prompt(
            {"id": "task-1", "title": "T", "description": "D", "notes": []},
            "run-1",
            branch="feature/x",
            worktree_path=Path("/repo/.worktrees/abc123"),
        )
        self.assertIn("Branch context: feature/x", prompt)
        self.assertIn("Working directory: /repo/.worktrees/abc123", prompt)
        self.assertIn("feature/x", prompt)

    def test_build_worker_prompt_omits_branch_context_when_no_branch(self) -> None:
        prompt = build_worker_prompt(
            {"id": "task-1", "title": "T", "description": "D", "notes": []},
            "run-1",
        )
        self.assertNotIn("Branch context", prompt)
        self.assertNotIn("Working directory", prompt)

    def test_worker_spawn_loop_calls_worktree_setup_when_branch_set(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = RunStore(root)
            run = store.create_run(initial_prompt="Plan.", workdir=root)
            orchestrator = FakeOrchestrator(root, run_id=run.id)
            run_tasks_path = tasks_file_path(root, run.id)
            tasks("create", path=run_tasks_path, title="T1", description="D1", branch="feature/x")

            ensure_calls: list[tuple] = []
            create_calls: list[tuple] = []
            fake_worktree = Path(tmp) / ".worktrees" / "fake"

            with patch("src.orchestration.driver.ensure_branch", side_effect=lambda r, b: ensure_calls.append((r, b))) as _eb, \
                 patch("src.orchestration.driver.create_worktree", return_value=fake_worktree, side_effect=lambda r, s, b: create_calls.append((r, s, b)) or fake_worktree) as _cw:
                run_orchestration_loop(root, run.id, orchestrator=orchestrator, run_store=store, poll_interval_seconds=0)

            self.assertEqual(len(ensure_calls), 1)
            self.assertEqual(ensure_calls[0][1], "feature/x")
            self.assertEqual(len(create_calls), 1)
            self.assertEqual(create_calls[0][2], "feature/x")

            worker_spec = next(s for s in orchestrator.created_specs if s.role == "worker")
            self.assertEqual(worker_spec.workdir, fake_worktree)
            self.assertEqual(worker_spec.worktree_path, fake_worktree)

    def test_worker_spawn_loop_skips_worktree_when_no_branch(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = RunStore(root)
            run = store.create_run(initial_prompt="Plan.", workdir=root)
            orchestrator = FakeOrchestrator(root, run_id=run.id)
            run_tasks_path = tasks_file_path(root, run.id)
            tasks("create", path=run_tasks_path, title="T1", description="D1")

            with patch("src.orchestration.driver.ensure_branch") as eb, \
                 patch("src.orchestration.driver.create_worktree") as cw:
                run_orchestration_loop(root, run.id, orchestrator=orchestrator, run_store=store, poll_interval_seconds=0)

            eb.assert_not_called()
            cw.assert_not_called()

            worker_spec = next(s for s in orchestrator.created_specs if s.role == "worker")
            self.assertEqual(worker_spec.workdir, root.resolve())
            self.assertIsNone(worker_spec.worktree_path)


if __name__ == "__main__":
    unittest.main()
