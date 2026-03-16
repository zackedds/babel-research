<example_reference>
def _utc_now():
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _tasks_load():
    if not os.path.exists(TASKS_PATH):
        return {"next_id": 1, "tasks": {}, "client_ids": {}}
    try:
        with open(TASKS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {"next_id": 1, "tasks": {}, "client_ids": {}}
    data.setdefault("next_id", 1)
    data.setdefault("tasks", {})
    data.setdefault("client_ids", {})
    return data


def _tasks_save(data):
    os.makedirs(os.path.dirname(TASKS_PATH) or ".", exist_ok=True)
    with open(TASKS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")


def _is_ready(task, tasks):
    if task.get("status") != "open":
        return False
    for blocker_id in task.get("blockers", []):
        blocker = tasks.get(blocker_id)
        if blocker and blocker.get("status") == "open":
            return False
    return True


def _full_task(task, tasks):
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
    }


def _compact_task(task, tasks):
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


def _raw_task(task):
    raw = dict(task)
    raw["notes"] = [dict(note) for note in task.get("notes", [])]
    raw["blockers"] = list(task.get("blockers", []))
    raw["blocked"] = list(task.get("blocked", []))
    return raw


def tasks(op, **kwargs):
    data = _tasks_load()
    table = data["tasks"]

    def require_task(task_id):
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
                return {"ok": True, "op": op, "result": {"created": False, "task": _full_task(table[existing], table)}}
        task_id = f"task-{data['next_id']}"
        data["next_id"] += 1
        now = _utc_now()
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
        }
        table[task_id] = task
        if client_id:
            data["client_ids"][client_id] = task_id
        _tasks_save(data)
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
        if task.get("status") != "open":
            raise ValueError("update requires task status open")
        if title is not None:
            task["title"] = title
        if description is not None:
            task["description"] = description
        task["updated_at"] = _utc_now()
        _tasks_save(data)
        return {"ok": True, "op": op, "result": _full_task(task, table)}

    if op == "list":
        view = kwargs.get("view", "open")
        include_full = bool(kwargs.get("include_full", False))
        include_findings = bool(kwargs.get("include_findings", False))
        if view == "include_findings":
            include_findings = True
            view = "all"
        limit = kwargs.get("limit")
        offset = int(kwargs.get("offset", 0) or 0)
        tasks_list = sorted(table.values(), key=lambda t: t["id"])
        if view == "open":
            tasks_list = [t for t in tasks_list if t.get("status") == "open"]
        elif view == "closed":
            tasks_list = [t for t in tasks_list if t.get("status") == "closed"]
        elif view == "ready":
            tasks_list = [t for t in tasks_list if _is_ready(t, table)]
        elif view != "all":
            raise ValueError(f"Unsupported view: {view}")
        if include_findings:
            rows = [_raw_task(t) for t in tasks_list]
            return json.dumps(rows, ensure_ascii=False)
        else:
            rows = [_full_task(t, table) if include_full else _compact_task(t, table) for t in tasks_list]
        start = max(0, offset)
        page = rows[start : start + limit] if isinstance(limit, int) else rows[start:]
        return {
            "ok": True,
            "op": op,
            "result": {"view": view, "total": len(rows), "offset": start, "count": len(page), "tasks": page},
        }

    if op == "note_append":
        task_id = kwargs.get("id")
        note = kwargs.get("note")
        if not task_id or not note:
            raise ValueError("note_append requires id and note")
        task = require_task(task_id)
        task.setdefault("notes", []).append({"ts": _utc_now(), "text": note})
        task["updated_at"] = _utc_now()
        _tasks_save(data)
        return {"ok": True, "op": op, "result": _full_task(task, table)}

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
            _tasks_save(data)
        return {"ok": True, "op": op, "result": {"changed": changed, "blocker_id": blocker_id, "blocked_id": blocked_id}}

    if op == "close":
        task_id = kwargs.get("id")
        if not task_id:
            raise ValueError("close requires id")
        reason = kwargs.get("reason", "")
        task = require_task(task_id)
        task["status"] = "closed"
        task["close_reason"] = reason
        task["updated_at"] = _utc_now()
        _tasks_save(data)
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
                    blocker["blocked"] = [x for x in blocker.get("blocked", []) if x != task_id]
            for blocked_id in task.get("blocked", []):
                blocked = table.get(blocked_id)
                if blocked:
                    blocked["blockers"] = [x for x in blocked.get("blockers", []) if x != task_id]
            if task.get("client_id"):
                data["client_ids"].pop(task["client_id"], None)
            del table[task_id]
            _tasks_save(data)
            return {"ok": True, "op": op, "result": {"deleted": True, "hard": True, "id": task_id}}
        task["status"] = "deleted"
        task["updated_at"] = _utc_now()
        _tasks_save(data)
        return {"ok": True, "op": op, "result": _full_task(task, table)}

    raise ValueError(f"Unsupported op: {op}")
</example_reference>
Write a tasks cli tool `tasks` for use by an agent. I have provided an example of a python api. It should support the same operations. Write to plan2.md (do NOT overwrite plan.md) in current directory root
