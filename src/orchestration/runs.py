from __future__ import annotations

import secrets
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from ..utils.json_store import load_json, locked_json_store, write_json_atomic
from ..utils.models import OrchestrationRun
from .state_paths import (
    legacy_runs_path,
    load_run_index,
    mutate_run_index,
    run_file_path,
    sessions_file_path,
    tasks_file_path,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RunStore:
    def __init__(self, root: Path, *, path: Path | None = None) -> None:
        self.root = root.resolve()
        self.path = path or legacy_runs_path(self.root)
        self._test_write_delay = 0.0

    def create_run(
        self,
        *,
        initial_prompt: str,
        workdir: Path,
        max_rounds: int | None = None,
        max_workers: int | None = None,
        debug: bool = False,
    ) -> OrchestrationRun:
        now = _utc_now()
        run = OrchestrationRun(
            id=secrets.token_hex(4),
            status="running",
            initial_prompt=initial_prompt,
            workdir=workdir.resolve(),
            max_rounds=max_rounds,
            max_workers=max_workers,
            rounds_completed=0,
            planner_session_ids=[],
            worker_waves=[],
            librarian_session_ids=[],
            claimed_task_ids=[],
            current_phase="planner",
            created_at=now,
            updated_at=now,
            debug=debug,
        )
        self._write_run_file(run)
        write_json_atomic(sessions_file_path(self.root, run.id), {"sessions": []})
        write_json_atomic(tasks_file_path(self.root, run.id), {"status": "preparing", "next_id": 1, "tasks": {}, "client_ids": {}})
        mutate_run_index(self.root, lambda index: self._append_index_entry(index, run.id))
        return run

    def get_run(self, run_id: str) -> OrchestrationRun | None:
        indexed = self._load_indexed_runs()
        if run_id in indexed:
            return indexed[run_id]
        return self._load_legacy_runs().get(run_id)

    def list_runs(self) -> list[OrchestrationRun]:
        combined = {**self._load_legacy_runs(), **self._load_indexed_runs()}
        return sorted(combined.values(), key=lambda item: item.created_at)

    def update_run(self, run: OrchestrationRun) -> OrchestrationRun:
        if run_file_path(self.root, run.id).exists():
            self._write_run_file(run)
            return run

        def update(runs: dict[str, OrchestrationRun]) -> OrchestrationRun:
            runs[run.id] = run
            return run

        return self._mutate_legacy_runs(update)

    def _deserialize_runs(self, raw: dict[str, object]) -> dict[str, OrchestrationRun]:
        runs: dict[str, OrchestrationRun] = {}
        for item in raw.get("runs", []):
            run = OrchestrationRun(
                id=item["id"],
                status=item["status"],
                initial_prompt=item["initial_prompt"],
                workdir=Path(item["workdir"]),
                max_rounds=item.get("max_rounds"),
                max_workers=item.get("max_workers"),
                rounds_completed=item.get("rounds_completed", 0),
                planner_session_ids=list(item.get("planner_session_ids", [])),
                worker_waves=[list(wave) for wave in item.get("worker_waves", [])],
                librarian_session_ids=list(item.get("librarian_session_ids", [])),
                claimed_task_ids=list(item.get("claimed_task_ids", [])),
                current_phase=item["current_phase"],
                created_at=datetime.fromisoformat(item["created_at"]),
                updated_at=datetime.fromisoformat(item["updated_at"]),
                debug=bool(item.get("debug", False)),
            )
            runs[run.id] = run
        return runs

    def _load_legacy_runs(self) -> dict[str, OrchestrationRun]:
        raw = load_json(self.path, default_factory=lambda: {"runs": []})
        return self._deserialize_runs(raw)

    def _mutate_legacy_runs(self, mutator) -> OrchestrationRun:
        with locked_json_store(self.path, default_factory=lambda: {"runs": []}) as raw:
            runs = self._deserialize_runs(raw)
            result = mutator(runs)
            self._write_legacy_runs(runs)
            return result

    def _load_indexed_runs(self) -> dict[str, OrchestrationRun]:
        index = load_run_index(self.root)
        run_ids = [item for item in index.get("run_ids", []) if isinstance(item, str)]
        runs: dict[str, OrchestrationRun] = {}
        for run_id in run_ids:
            path = run_file_path(self.root, run_id)
            raw = load_json(path, default_factory=dict)
            if raw:
                run = self._deserialize_run(raw)
                runs[run.id] = run
        return runs

    def _deserialize_run(self, item: dict[str, object]) -> OrchestrationRun:
        return OrchestrationRun(
            id=item["id"],
            status=item["status"],
            initial_prompt=item["initial_prompt"],
            workdir=Path(item["workdir"]),
            max_rounds=item.get("max_rounds"),
            max_workers=item.get("max_workers"),
            rounds_completed=item.get("rounds_completed", 0),
            planner_session_ids=list(item.get("planner_session_ids", [])),
            worker_waves=[list(wave) for wave in item.get("worker_waves", [])],
            librarian_session_ids=list(item.get("librarian_session_ids", [])),
            claimed_task_ids=list(item.get("claimed_task_ids", [])),
            current_phase=item["current_phase"],
            created_at=datetime.fromisoformat(item["created_at"]),
            updated_at=datetime.fromisoformat(item["updated_at"]),
            debug=bool(item.get("debug", False)),
        )

    def _write_run_file(self, run: OrchestrationRun) -> None:
        path = run_file_path(self.root, run.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(path, self._serialize_run(run))

    def _serialize_run(self, run: OrchestrationRun) -> dict[str, object]:
        return {
            **asdict(run),
            "workdir": str(run.workdir),
            "created_at": run.created_at.isoformat(),
            "updated_at": run.updated_at.isoformat(),
        }

    def _write_legacy_runs(self, runs: dict[str, OrchestrationRun]) -> None:
        payload = {"runs": [self._serialize_run(run) for run in sorted(runs.values(), key=lambda item: item.created_at)]}
        write_json_atomic(self.path, payload)

    @staticmethod
    def _append_index_entry(index: dict[str, object], run_id: str) -> None:
        run_ids = [item for item in index.get("run_ids", []) if isinstance(item, str)]
        if run_id not in run_ids:
            run_ids.append(run_id)
        index["run_ids"] = run_ids
        index["latest_run_id"] = run_id
        index["active_run_id"] = run_id
