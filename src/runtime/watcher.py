from __future__ import annotations

import argparse
import atexit
import os
import signal
import sys
import traceback
from pathlib import Path

from ..orchestration.sessions import append_log_line, watch_session_completion
from ..orchestration.state_paths import session_log_path


def _install_signal_handlers(log_path: Path) -> None:
    """Install signal handlers that convert SIGTERM/SIGHUP into SystemExit.

    Python's default SIGTERM handler is SIG_DFL which terminates the process
    immediately *without* running finally blocks or atexit handlers.  By
    raising SystemExit we allow the finally block in watch_session_completion
    to write "watcher stopping" and mark the session terminal state.
    """

    def _handle_term(signum: int, _frame: object) -> None:
        sig_name = signal.Signals(signum).name
        append_log_line(log_path, f"watcher received {sig_name} (pid={os.getpid()}); raising SystemExit")
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, _handle_term)
    signal.signal(signal.SIGHUP, _handle_term)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.runtime.watcher")
    parser.add_argument("--root", required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--tmux-session", required=True)
    parser.add_argument("--prompt-prefix", required=True)
    parser.add_argument("--inactivity-timeout", type=float, default=600.0)
    parser.add_argument("--debug", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    log_path = session_log_path(Path(args.root), args.session_id, args.run_id)
    append_log_line(
        log_path,
        f"watcher process starting pid={os.getpid()} ppid={os.getppid()} "
        f"pgid={os.getpgrp()} sid={os.getsid(0)} session_id={args.session_id}",
    )
    _install_signal_handlers(log_path)

    # atexit safety net: if the process is torn down without reaching the
    # finally block (e.g. an unhandled BaseException subclass other than
    # SystemExit/KeyboardInterrupt, or an interpreter crash that still runs
    # atexit), log the fact so we have a breadcrumb.
    _atexit_fired = False

    def _atexit_log() -> None:
        nonlocal _atexit_fired
        _atexit_fired = True
        append_log_line(log_path, f"watcher atexit handler fired pid={os.getpid()}")

    atexit.register(_atexit_log)

    try:
        watch_session_completion(
            Path(args.root),
            args.run_id,
            args.session_id,
            args.tmux_session,
            args.prompt_prefix,
            inactivity_timeout_seconds=args.inactivity_timeout,
            debug=args.debug,
        )
    except BaseException:
        append_log_line(log_path, f"watcher entrypoint caught {sys.exc_info()[0].__name__}")
        append_log_line(log_path, traceback.format_exc().rstrip())
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
