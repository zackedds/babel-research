from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from src.orchestration.runs import RunStore
from src.orchestration.sessions import load_sessions
from src.orchestration.state_paths import sessions_file_path
from src.runtime.activity import activity_snapshot, main as activity_main
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
                "round_index": session.round_index,
                "task_id": session.task_id,
                "task_title": session.task_title,
                "task_description": session.task_description,
                "finished_at": session.finished_at.isoformat() if session.finished_at is not None else None,
            }
            for session in sessions
        ]
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


class ActivitySnapshotTest(unittest.TestCase):
    class FakeTmux:
        def __init__(self, sessions: set[str]) -> None:
            self.sessions = sessions

        def has_session(self, session_name: str) -> bool:
            return session_name in self.sessions

    def test_activity_snapshot_sorts_running_and_history(self) -> None:
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
                    rounds_completed=1,
                    planner_session_ids=["planner-1", "planner-2"],
                    worker_waves=[["worker-1", "worker-2"]],
                    claimed_task_ids=["task-1", "task-2"],
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
                        started_at=now - timedelta(minutes=4),
                        terminal_outcome="completed",
                        round_index=1,
                        finished_at=now - timedelta(minutes=1),
                    ),
                    AgentSession(
                        id="planner-2",
                        tmux_session="orch-planner-2",
                        role="planner",
                        runtime="codex",
                        workdir=root,
                        full_prompt="planner prompt",
                        status="running",
                        started_at=now - timedelta(seconds=30),
                        round_index=2,
                    ),
                    AgentSession(
                        id="worker-1",
                        tmux_session="orch-worker-1",
                        role="worker",
                        runtime="codex",
                        workdir=root,
                        full_prompt="worker prompt",
                        status="stopped",
                        started_at=now - timedelta(minutes=3),
                        terminal_outcome="failed",
                        round_index=1,
                        task_id="task-1",
                        task_title="Stabilize driver",
                        task_description="Lock down duplicate scheduling.",
                        finished_at=now - timedelta(minutes=2),
                    ),
                    AgentSession(
                        id="worker-2",
                        tmux_session="orch-worker-2",
                        role="worker",
                        runtime="codex",
                        workdir=root,
                        full_prompt="worker prompt",
                        status="running",
                        started_at=now - timedelta(seconds=5),
                        round_index=2,
                        task_id="task-2",
                        task_title="Add feed",
                        task_description="Build the activity feed.",
                    ),
                ],
            )

            payload = activity_snapshot(root, run_id=updated.id, tmux_client=self.FakeTmux({"orch-planner-2", "orch-worker-2"}))

            self.assertEqual(payload["run"]["id"], updated.id)
            self.assertEqual([row["id"] for row in payload["running"]], ["worker-2", "planner-2"])
            self.assertEqual([row["id"] for row in payload["history"]], ["planner-1", "worker-1"])
            self.assertEqual(payload["history"][0]["display_status"], "finished")
            self.assertEqual(payload["history"][1]["display_status"], "failed")
            self.assertEqual(payload["running"][0]["task_title"], "Add feed")
            self.assertIsNone(payload["history"][0]["failure_reason"])

    def test_load_sessions_accepts_legacy_entries_without_activity_metadata(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / ".babel-agent" / "sessions.json"
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(
                json.dumps(
                    {
                        "sessions": [
                            {
                                "id": "planner-1",
                                "tmux_session": "orch-planner-1",
                                "role": "planner",
                                "runtime": "codex",
                                "workdir": str(root),
                                "full_prompt": "prompt",
                                "status": "stopped",
                                "started_at": "2026-03-16T10:00:00+00:00",
                                "terminal_outcome": "completed",
                            }
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            sessions = load_sessions(state_path)

            self.assertIsNone(sessions["planner-1"].round_index)
            self.assertIsNone(sessions["planner-1"].task_id)
            self.assertIsNone(sessions["planner-1"].finished_at)
            self.assertIsNone(sessions["planner-1"].failure_reason)
            self.assertIsNone(sessions["planner-1"].last_observed_at)

    def test_activity_module_main_prints_snapshot_json(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = RunStore(root)
            run = store.create_run(initial_prompt="Plan.", workdir=root)
            _write_sessions(sessions_file_path(root, run.id), [])

            stdout = __import__("io").StringIO()
            with __import__("contextlib").redirect_stdout(stdout):
                code = activity_main(["--root", str(root), "--run-id", run.id])

            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["run"]["id"], run.id)

    def test_activity_snapshot_reconciles_missing_tmux_into_history(self) -> None:
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

            payload = activity_snapshot(root, run_id=updated.id, tmux_client=self.FakeTmux(set()))

            self.assertEqual(payload["running"], [])
            self.assertEqual(payload["history"][0]["id"], "planner-1")
            self.assertEqual(payload["history"][0]["display_status"], "failed")
            self.assertEqual(payload["history"][0]["failure_source"], "tmux_missing")


if __name__ == "__main__":
    unittest.main()
