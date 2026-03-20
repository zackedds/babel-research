from __future__ import annotations

from pathlib import Path

from ..utils.json_store import load_json, locked_json_store, write_json_atomic

STATE_DIR = Path(".babel-agent")
RUNS_DIR = STATE_DIR / "runs"
RUN_INDEX_PATH = STATE_DIR / "index.json"
LEGACY_RUNS_PATH = STATE_DIR / "runs.json"
LEGACY_SESSIONS_PATH = STATE_DIR / "sessions.json"
RUN_FILENAME = "run.json"
SESSIONS_FILENAME = "sessions.json"
TASKS_FILENAME = "tasks.json"
LOGS_DIRNAME = "logs"
DRIVER_LOG_FILENAME = "driver.log"


def state_dir(root: Path) -> Path:
    return root.resolve() / STATE_DIR


def run_index_path(root: Path) -> Path:
    return root.resolve() / RUN_INDEX_PATH


def runs_dir(root: Path) -> Path:
    return root.resolve() / RUNS_DIR


def run_dir(root: Path, run_id: str) -> Path:
    return runs_dir(root) / run_id


def run_file_path(root: Path, run_id: str) -> Path:
    return run_dir(root, run_id) / RUN_FILENAME


def logs_dir(root: Path, run_id: str | None = None) -> Path:
    if run_id is None:
        return state_dir(root) / LOGS_DIRNAME
    return run_dir(root, run_id) / LOGS_DIRNAME


def driver_log_path(root: Path, run_id: str | None = None) -> Path:
    return logs_dir(root, run_id) / DRIVER_LOG_FILENAME


def session_log_path(root: Path, session_id: str, run_id: str | None = None) -> Path:
    return logs_dir(root, run_id) / f"session-{session_id}.log"


def sessions_file_path(root: Path, run_id: str | None = None) -> Path:
    if run_id is None:
        return root.resolve() / LEGACY_SESSIONS_PATH
    return run_dir(root, run_id) / SESSIONS_FILENAME


def tasks_file_path(root: Path, run_id: str) -> Path:
    return run_dir(root, run_id) / TASKS_FILENAME


def legacy_runs_path(root: Path) -> Path:
    return root.resolve() / LEGACY_RUNS_PATH


def load_run_index(root: Path) -> dict[str, object]:
    return load_json(
        run_index_path(root),
        default_factory=lambda: {"run_ids": [], "latest_run_id": None, "active_run_id": None},
    )


def mutate_run_index(root: Path, mutator):
    path = run_index_path(root)
    with locked_json_store(
        path,
        default_factory=lambda: {"run_ids": [], "latest_run_id": None, "active_run_id": None},
    ) as raw:
        raw.setdefault("run_ids", [])
        raw.setdefault("latest_run_id", None)
        raw.setdefault("active_run_id", None)
        result = mutator(raw)
        write_json_atomic(path, raw)
        return result


def resolve_default_run_id(root: Path) -> str | None:
    index = load_run_index(root)
    active = index.get("active_run_id")
    if isinstance(active, str) and active:
        return active
    latest = index.get("latest_run_id")
    if isinstance(latest, str) and latest:
        return latest
    return None


def find_workspace_root(start: Path, run_id: str | None = None) -> Path:
    resolved = start.resolve()
    current = resolved
    fallback = resolved
    while True:
        if (current / STATE_DIR).is_dir():
            if run_id is None:
                return current
            if (current / RUNS_DIR / run_id).is_file():
                return current
            fallback = current
        parent = current.parent
        if parent == current:
            return fallback
        current = parent


def resolve_default_tasks_path(root: Path) -> Path:
    effective_root = find_workspace_root(root)
    run_id = resolve_default_run_id(effective_root)
    if run_id is None:
        raise RuntimeError(f"No active run found in {effective_root}")
    return tasks_file_path(effective_root, run_id)
