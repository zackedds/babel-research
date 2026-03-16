from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

from ..orchestration.sessions import append_log_line, watch_session_completion
from ..orchestration.state_paths import session_log_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.runtime.watcher")
    parser.add_argument("--root", required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--tmux-session", required=True)
    parser.add_argument("--prompt-prefix", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    log_path = session_log_path(Path(args.root), args.session_id, args.run_id)
    try:
        watch_session_completion(
            Path(args.root),
            args.run_id,
            args.session_id,
            args.tmux_session,
            args.prompt_prefix,
        )
    except Exception:
        append_log_line(log_path, "watcher entrypoint raised an exception")
        append_log_line(log_path, traceback.format_exc().rstrip())
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
