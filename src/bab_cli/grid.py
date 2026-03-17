from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ..orchestration.sessions import load_sessions
from ..orchestration.state_paths import resolve_default_run_id, sessions_file_path
from ..runtime.tmux import TmuxClient


def run_grid(cwd: Path, run_id: str | None = None) -> None:
    state_root = cwd.resolve()

    if run_id is None:
        run_id = resolve_default_run_id(state_root)
    if run_id is None:
        raise RuntimeError("No active run found. Specify --run-id.")

    sessions_path = sessions_file_path(state_root, run_id)
    all_sessions = load_sessions(sessions_path)

    tmux = TmuxClient()

    active = [
        s
        for s in all_sessions.values()
        if s.status == "running" and tmux.has_session(s.tmux_session)
    ]

    if not active:
        raise RuntimeError(f"No active sessions for run {run_id}.")

    grid_name = f"bab-grid-{run_id}"

    if tmux.has_session(grid_name):
        print(f"Grid session {grid_name!r} already exists — attaching.")
        _attach(grid_name)
        return

    first = active[0]
    subprocess.run(
        ["tmux", "new-session", "-d", "-s", grid_name,
         f"TMUX='' tmux attach-session -t {first.tmux_session}"],
        check=True,
    )

    for s in active[1:]:
        subprocess.run(
            ["tmux", "split-window", "-t", grid_name,
             f"TMUX='' tmux attach-session -t {s.tmux_session}"],
            check=True,
        )

    subprocess.run(["tmux", "select-layout", "-t", grid_name, "tiled"], check=True)

    _attach(grid_name)


def _attach(grid_name: str) -> None:
    if os.environ.get("TMUX"):
        print(f"Already in tmux. Switch with:  tmux switch-client -t {grid_name!r}")
    else:
        os.execvp("tmux", ["tmux", "attach-session", "-t", grid_name])
