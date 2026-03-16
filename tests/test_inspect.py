from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from src.orchestration.runs import RunStore
from src.orchestration.state_paths import sessions_file_path, tasks_file_path
from src.runtime.inspect import inspect_run
from src.tasks.store import tasks
from src.utils.models import AgentSession, OrchestrationRun


def _write_sessions(path: Path, sessions: list[AgentSession]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "sessions": [
            {
                "id": session.id,
                "tmux_session": session.tmux_session,
                "role": session.role,
                "runtime": session.runtime,
                "workdir": str(session.workdir),
                "full_prompt": session.full_prompt,
                "status": session.status,
                "started_at": session.started_at.isoformat(),
                "terminal_outcome": session.terminal_outcome,
            }
            for session in sessions
        ]
    }
    path.write_text(__import__("json").dumps(payload, indent=2) + "\n", encoding="utf-8")


class InspectRunTest(unittest.TestCase):
    class FakeTmux:
        def __init__(self, sessions: set[str]) -> None:
            self.sessions = sessions

        def has_session(self, session_name: str) -> bool:
            return session_name in self.sessions

    def test_inspect_run_composes_run_sessions_and_outstanding_tasks(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = RunStore(root)
            run = store.create_run(initial_prompt="Plan.", workdir=root)
            sessions_path = sessions_file_path(root, run.id)
            run_tasks_path = tasks_file_path(root, run.id)
            now = datetime.now(timezone.utc)
            updated = store.update_run(
                OrchestrationRun(
                    id=run.id,
                    status="running",
                    initial_prompt=run.initial_prompt,
                    workdir=run.workdir,
                    max_rounds=run.max_rounds,
                    max_workers=run.max_workers,
                    rounds_completed=run.rounds_completed,
                    planner_session_ids=["planner-1"],
                    worker_waves=[["worker-1", "worker-2"]],
                    claimed_task_ids=["task-1"],
                    current_phase="worker",
                    created_at=run.created_at,
                    updated_at=now,
                )
            )
            _write_sessions(
                sessions_path,
                [
                    AgentSession(
                        id="planner-1",
                        tmux_session="orch-planner-1",
                        role="planner",
                        runtime="codex",
                        workdir=root,
                        full_prompt="planner prompt",
                        status="stopped",
                        started_at=now,
                        terminal_outcome="completed",
                    ),
                    AgentSession(
                        id="worker-1",
                        tmux_session="orch-worker-1",
                        role="worker",
                        runtime="codex",
                        workdir=root,
                        full_prompt="worker prompt",
                        status="stopped",
                        started_at=now,
                        terminal_outcome="completed",
                    ),
                    AgentSession(
                        id="worker-2",
                        tmux_session="orch-worker-2",
                        role="worker",
                        runtime="codex",
                        workdir=root,
                        full_prompt="worker prompt",
                        status="running",
                        started_at=now,
                        terminal_outcome=None,
                    ),
                ],
            )
            blocker = tasks("create", path=run_tasks_path, title="Blocker", description="Block worker")
            blocked = tasks("create", path=run_tasks_path, title="Blocked", description="Wait on blocker")
            tasks("dep_add", path=run_tasks_path, blocker_id=blocker["result"]["task"]["id"], blocked_id=blocked["result"]["task"]["id"])

            payload = inspect_run(root, run_id=updated.id, tmux_client=self.FakeTmux({"orch-worker-2"}))

            self.assertEqual(payload["run"]["id"], updated.id)
            self.assertEqual(payload["run"]["status"], "running")
            self.assertEqual(payload["run"]["current_phase"], "worker")
            self.assertIsNone(payload["run"]["max_rounds"])
            self.assertIsNone(payload["run"]["max_workers"])
            self.assertEqual(payload["run"]["rounds_completed"], 0)
            self.assertEqual(payload["planner_sessions"][0]["status"], "stopped")
            self.assertEqual(payload["planner_sessions"][0]["terminal_outcome"], "completed")
            self.assertEqual(payload["worker_waves"][0]["index"], 1)
            self.assertEqual(payload["worker_waves"][0]["status"], "running")
            self.assertEqual(len(payload["worker_waves"][0]["workers"]), 2)
            self.assertEqual(payload["outstanding_tasks"]["total"], 2)
            self.assertEqual(payload["outstanding_tasks"]["ready"], 1)
            self.assertEqual(payload["outstanding_tasks"]["blocked"], 1)
            self.assertIsNone(payload["worker_waves"][0]["workers"][1]["failure_reason"])

    def test_inspect_wave_marks_failed_terminal_outcome_as_failed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = RunStore(root)
            run = store.create_run(initial_prompt="Plan.", workdir=root)
            sessions_path = sessions_file_path(root, run.id)
            now = datetime.now(timezone.utc)
            updated = store.update_run(
                OrchestrationRun(
                    id=run.id,
                    status="running",
                    initial_prompt=run.initial_prompt,
                    workdir=run.workdir,
                    max_rounds=run.max_rounds,
                    max_workers=run.max_workers,
                    rounds_completed=run.rounds_completed,
                    planner_session_ids=["planner-1"],
                    worker_waves=[["worker-1"]],
                    claimed_task_ids=[],
                    current_phase="worker",
                    created_at=run.created_at,
                    updated_at=now,
                )
            )
            _write_sessions(
                sessions_path,
                [
                    AgentSession(
                        id="planner-1",
                        tmux_session="orch-planner-1",
                        role="planner",
                        runtime="codex",
                        workdir=root,
                        full_prompt="planner prompt",
                        status="stopped",
                        started_at=now,
                        terminal_outcome="completed",
                    ),
                    AgentSession(
                        id="worker-1",
                        tmux_session="orch-worker-1",
                        role="worker",
                        runtime="codex",
                        workdir=root,
                        full_prompt="worker prompt",
                        status="stopped",
                        started_at=now,
                        terminal_outcome="failed",
                    ),
                ],
            )

            payload = inspect_run(root, run_id=updated.id, tmux_client=self.FakeTmux(set()))

            self.assertEqual(payload["worker_waves"][0]["status"], "failed")

    def test_inspect_run_defaults_to_latest_run(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = RunStore(root)
            first = store.create_run(initial_prompt="first", workdir=root)
            second = store.create_run(initial_prompt="second", workdir=root)

            payload = inspect_run(root)

            self.assertEqual(payload["run"]["id"], second.id)
            self.assertNotEqual(payload["run"]["id"], first.id)

    def test_inspect_reconciles_missing_tmux_session_as_failed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = RunStore(root)
            run = store.create_run(initial_prompt="Plan.", workdir=root)
            sessions_path = sessions_file_path(root, run.id)
            now = datetime.now(timezone.utc)
            updated = store.update_run(
                OrchestrationRun(
                    id=run.id,
                    status="running",
                    initial_prompt=run.initial_prompt,
                    workdir=run.workdir,
                    max_rounds=run.max_rounds,
                    max_workers=run.max_workers,
                    rounds_completed=0,
                    planner_session_ids=["planner-1"],
                    worker_waves=[],
                    claimed_task_ids=[],
                    current_phase="planner",
                    created_at=run.created_at,
                    updated_at=now,
                )
            )
            _write_sessions(
                sessions_path,
                [
                    AgentSession(
                        id="planner-1",
                        tmux_session="orch-planner-1",
                        role="planner",
                        runtime="codex",
                        workdir=root,
                        full_prompt="planner prompt",
                        status="running",
                        started_at=now,
                    )
                ],
            )

            payload = inspect_run(root, run_id=updated.id, tmux_client=self.FakeTmux(set()))

            planner = payload["planner_sessions"][0]
            self.assertEqual(planner["status"], "stopped")
            self.assertEqual(planner["terminal_outcome"], "failed")
            self.assertEqual(planner["failure_source"], "tmux_missing")
            self.assertIn("tmux session disappeared", planner["failure_reason"])


if __name__ == "__main__":
    unittest.main()
