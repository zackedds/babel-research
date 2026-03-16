from __future__ import annotations

import datetime as _dt
import json
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..orchestration.state_paths import LEGACY_TASKS_PATH

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None

DEFAULT_TASKS_PATH = LEGACY_TASKS_PATH


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _empty_store() -> dict[str, Any]:
    return {"status": "preparing", "next_id": 1, "tasks": {}, "client_ids": {}}


def _tasks_load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty_store()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _empty_store()
    data.setdefault("next_id", 1)
    data.setdefault("tasks", {})
    data.setdefault("client_ids", {})
    data.setdefault("status", "preparing")
    return data


def _tasks_save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


@contextmanager
def _locked_store(path: Path) -> Any:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f"{path.name}.lock")
    lock_path.touch(exist_ok=True)
    with lock_path.open("r+", encoding="utf-8") as lock_file:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield _tasks_load(path)
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _is_ready(task: dict[str, Any], tasks: dict[str, dict[str, Any]]) -> bool:
    if task.get("status") != "open":
        return False
    for blocker_id in task.get("blockers", []):
        blocker = tasks.get(blocker_id)
        if blocker and blocker.get("status") not in {"closed", "deleted"}:
            return False
    return True


def _full_task(task: dict[str, Any], tasks: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": task["id"],
        "title": task["title"],
        "description": task["description"],
        "status": task["status"],
        "ready": _is_ready(task, tasks),
        "notes": list(task.get("notes", [])),
        "blockers": list(task.get("blockers", [])),
        "blocked": list(task.get("blocked", [])),
        "created_at": task.get("created_at", ""),
        "updated_at": task.get("updated_at", ""),
        "close_reason": task.get("close_reason", ""),
        "client_id": task.get("client_id", ""),
        "branch": task.get("branch", ""),
    }


def _compact_task(task: dict[str, Any], tasks: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": task["id"],
        "title": task["title"],
        "status": task["status"],
        "ready": _is_ready(task, tasks),
        "blockers": list(task.get("blockers", [])),
        "blocked": list(task.get("blocked", [])),
        "notes_count": len(task.get("notes", [])),
        "updated_at": task.get("updated_at", ""),
    }


def _raw_task(task: dict[str, Any]) -> dict[str, Any]:
    raw = dict(task)
    raw["notes"] = [dict(note) for note in task.get("notes", [])]
    raw["blockers"] = list(task.get("blockers", []))
    raw["blocked"] = list(task.get("blocked", []))
    return raw


def tasks(op: str, *, path: Path = DEFAULT_TASKS_PATH, **kwargs: Any) -> dict[str, Any] | list[dict[str, Any]]:
    delay = float(kwargs.pop("_test_delay", 0) or 0)

    with _locked_store(path) as data:
        table = data["tasks"]

        def require_task(task_id: str) -> dict[str, Any]:
            task = table.get(task_id)
            if task is None:
                raise ValueError(f"Unknown task id: {task_id}")
            return task

        if op == "create":
            title = kwargs.get("title")
            description = kwargs.get("description")
            client_id = kwargs.get("client_id")
            if not title or not description:
                raise ValueError("create requires title and description")
            if client_id:
                existing = data["client_ids"].get(client_id)
                if existing and existing in table:
                    return {
                        "ok": True,
                        "op": op,
                        "result": {"created": False, "task": _full_task(table[existing], table)},
                    }
            if delay:
                time.sleep(delay)
            task_id = f"task-{data['next_id']}"
            data["next_id"] += 1
            now = _utc_now()
            branch = kwargs.get("branch", "")
            task = {
                "id": task_id,
                "title": title,
                "description": description,
                "status": "open",
                "created_at": now,
                "updated_at": now,
                "notes": [],
                "blockers": [],
                "blocked": [],
                "close_reason": "",
                "client_id": client_id or "",
                "branch": branch or "",
            }
            table[task_id] = task
            if client_id:
                data["client_ids"][client_id] = task_id
            _tasks_save(path, data)
            return {"ok": True, "op": op, "result": {"created": True, "task": _full_task(task, table)}}

        if op == "get":
            task_id = kwargs.get("id")
            if not task_id:
                raise ValueError("get requires id")
            return {"ok": True, "op": op, "result": _full_task(require_task(task_id), table)}

        if op == "update":
            task_id = kwargs.get("id")
            title = kwargs.get("title")
            description = kwargs.get("description")
            if not task_id:
                raise ValueError("update requires id")
            if title is None and description is None:
                raise ValueError("update requires title and/or description")
            task = require_task(task_id)
            if task.get("status") not in {"open", "assigned"}:
                raise ValueError("update requires task status open or assigned")
            task["title"] = title if title is not None else task["title"]
            task["description"] = description if description is not None else task["description"]
            task["updated_at"] = _utc_now()
            _tasks_save(path, data)
            return {"ok": True, "op": op, "result": _full_task(task, table)}

        if op == "list":
            view = kwargs.get("view", "open")
            include_full = bool(kwargs.get("include_full", False))
            limit = kwargs.get("limit")
            offset = int(kwargs.get("offset", 0) or 0)
            tasks_list = sorted(table.values(), key=lambda task: task["id"])
            if view == "open":
                tasks_list = [task for task in tasks_list if task.get("status") in {"open", "assigned"}]
            elif view == "assigned":
                tasks_list = [task for task in tasks_list if task.get("status") == "assigned"]
            elif view == "closed":
                tasks_list = [task for task in tasks_list if task.get("status") == "closed"]
            elif view == "ready":
                tasks_list = [task for task in tasks_list if _is_ready(task, table)]
            elif view != "all":
                raise ValueError(f"Unsupported view: {view}")

            rows = [_full_task(task, table) if include_full else _compact_task(task, table) for task in tasks_list]
            start = max(0, offset)
            page = rows[start : start + limit] if isinstance(limit, int) else rows[start:]
            return {
                "ok": True,
                "op": op,
                "result": {
                    "status": data.get("status", "preparing"),
                    "view": view,
                    "total": len(rows),
                    "offset": start,
                    "count": len(page),
                    "tasks": page,
                },
            }

        if op == "prepare":
            data["status"] = "preparing"
            _tasks_save(path, data)
            return {"ok": True, "op": op, "result": {"status": data["status"]}}

        if op == "ready":
            data["status"] = "ready"
            _tasks_save(path, data)
            return {"ok": True, "op": op, "result": {"status": data["status"]}}

        if op == "note_append":
            task_id = kwargs.get("id")
            note = kwargs.get("note")
            if not task_id or not note:
                raise ValueError("note_append requires id and note")
            task = require_task(task_id)
            now = _utc_now()
            task.setdefault("notes", []).append({"ts": now, "text": note})
            task["updated_at"] = now
            _tasks_save(path, data)
            return {"ok": True, "op": op, "result": _full_task(task, table)}

        if op == "assign":
            task_id = kwargs.get("id")
            if not task_id:
                raise ValueError("assign requires id")
            task = require_task(task_id)
            status = task.get("status")
            if status == "assigned":
                return {"ok": True, "op": op, "result": {"changed": False, "task": _full_task(task, table)}}
            if status != "open":
                raise ValueError("assign requires task status open")
            if not _is_ready(task, table):
                raise ValueError("assign requires task ready")
            task["status"] = "assigned"
            task["updated_at"] = _utc_now()
            _tasks_save(path, data)
            return {"ok": True, "op": op, "result": {"changed": True, "task": _full_task(task, table)}}

        if op == "dep_add":
            blocker_id = kwargs.get("blocker_id")
            blocked_id = kwargs.get("blocked_id")
            if not blocker_id or not blocked_id:
                raise ValueError("dep_add requires blocker_id and blocked_id")
            if blocker_id == blocked_id:
                raise ValueError("A task cannot depend on itself")
            blocker = require_task(blocker_id)
            blocked = require_task(blocked_id)
            changed = False
            if blocker_id not in blocked.setdefault("blockers", []):
                blocked["blockers"].append(blocker_id)
                changed = True
            if blocked_id not in blocker.setdefault("blocked", []):
                blocker["blocked"].append(blocked_id)
                changed = True
            if changed:
                now = _utc_now()
                blocker["updated_at"] = now
                blocked["updated_at"] = now
                _tasks_save(path, data)
            return {
                "ok": True,
                "op": op,
                "result": {"changed": changed, "blocker_id": blocker_id, "blocked_id": blocked_id},
            }

        if op == "close":
            task_id = kwargs.get("id")
            if not task_id:
                raise ValueError("close requires id")
            reason = kwargs.get("reason", "")
            task = require_task(task_id)
            if task.get("status") not in {"open", "assigned"}:
                raise ValueError("close requires task status open or assigned")
            task["status"] = "closed"
            task["close_reason"] = reason
            task["updated_at"] = _utc_now()
            _tasks_save(path, data)
            return {"ok": True, "op": op, "result": _full_task(task, table)}

        if op == "delete":
            task_id = kwargs.get("id")
            if not task_id:
                raise ValueError("delete requires id")
            hard = bool(kwargs.get("hard", False))
            task = require_task(task_id)
            if hard:
                for blocker_id in task.get("blockers", []):
                    blocker = table.get(blocker_id)
                    if blocker:
                        blocker["blocked"] = [item for item in blocker.get("blocked", []) if item != task_id]
                for blocked_id in task.get("blocked", []):
                    blocked = table.get(blocked_id)
                    if blocked:
                        blocked["blockers"] = [item for item in blocked.get("blockers", []) if item != task_id]
                if task.get("client_id"):
                    data["client_ids"].pop(task["client_id"], None)
                del table[task_id]
                _tasks_save(path, data)
                return {"ok": True, "op": op, "result": {"deleted": True, "hard": True, "id": task_id}}
            task["status"] = "deleted"
            task["updated_at"] = _utc_now()
            _tasks_save(path, data)
            return {"ok": True, "op": op, "result": _full_task(task, table)}

        raise ValueError(f"Unsupported op: {op}")
