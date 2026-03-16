from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ..orchestration.state_paths import resolve_default_tasks_path, tasks_file_path
from .store import tasks


GLOBAL_OPTION_NAMES = {"--tasks-path", "--run-id"}


def _normalize_global_args(argv: list[str]) -> list[str]:
    prefix: list[str] = []
    rest: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in GLOBAL_OPTION_NAMES:
            prefix.append(token)
            if index + 1 >= len(argv):
                raise SystemExit(f"{token} requires a value")
            prefix.append(argv[index + 1])
            index += 2
            continue
        rest.append(token)
        index += 1
    return [*prefix, *rest]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tasks")
    parser.add_argument("--tasks-path", type=Path, default=None, help="Override the task store path")
    parser.add_argument("--run-id", default=None, help="Resolve the per-run task store by run id")

    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create")
    create.add_argument("--title", required=True)
    create.add_argument("--description", required=True)
    create.add_argument("--client-id")
    create.add_argument("--branch", default=None)

    get = subparsers.add_parser("get")
    get.add_argument("--id", required=True)

    update = subparsers.add_parser("update")
    update.add_argument("--id", required=True)
    update.add_argument("--title")
    update.add_argument("--description")

    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--view", choices=("open", "assigned", "closed", "ready", "all"), default="open")
    list_parser.add_argument("--include-full", action="store_true")
    list_parser.add_argument("--limit", type=int)
    list_parser.add_argument("--offset", type=int, default=0)

    subparsers.add_parser("ready")

    note_append = subparsers.add_parser("note-append")
    note_append.add_argument("--id", required=True)
    note_append.add_argument("--note", required=True)

    assign = subparsers.add_parser("assign")
    assign.add_argument("--id", required=True)

    dep_add = subparsers.add_parser("dep-add")
    dep_add.add_argument("--blocker-id", required=True)
    dep_add.add_argument("--blocked-id", required=True)

    close = subparsers.add_parser("close")
    close.add_argument("--id", required=True)
    close.add_argument("--reason", default="")

    delete = subparsers.add_parser("delete")
    delete.add_argument("--id", required=True)
    delete.add_argument("--hard", action="store_true")

    return parser


def _command_kwargs(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    command = args.command
    if command == "create":
        return command, {"title": args.title, "description": args.description, "client_id": args.client_id, "branch": args.branch}
    if command == "get":
        return command, {"id": args.id}
    if command == "update":
        return command, {"id": args.id, "title": args.title, "description": args.description}
    if command == "list":
        return command, {"view": args.view, "include_full": args.include_full, "limit": args.limit, "offset": args.offset}
    if command == "ready":
        return command, {}
    if command == "note-append":
        return "note_append", {"id": args.id, "note": args.note}
    if command == "assign":
        return command, {"id": args.id}
    if command == "dep-add":
        return "dep_add", {"blocker_id": args.blocker_id, "blocked_id": args.blocked_id}
    if command == "close":
        return command, {"id": args.id, "reason": args.reason}
    if command == "delete":
        return command, {"id": args.id, "hard": args.hard}
    raise ValueError(f"Unsupported command: {command}")


def _resolve_tasks_path(args: argparse.Namespace) -> Path:
    if args.tasks_path is not None:
        return args.tasks_path
    if args.run_id is not None:
        return tasks_file_path(Path.cwd(), args.run_id)
    return resolve_default_tasks_path(Path.cwd())


def main(argv: list[str] | None = None) -> int:
    argv = _normalize_global_args(list(sys.argv[1:] if argv is None else argv))
    parser = build_parser()
    args = parser.parse_args(argv)

    op, kwargs = _command_kwargs(args)
    tasks_path = _resolve_tasks_path(args)

    try:
        result = tasks(op, path=tasks_path, **kwargs)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
