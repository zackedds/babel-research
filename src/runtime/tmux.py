from __future__ import annotations

import shlex
import subprocess
import threading
import time
import uuid
from collections import defaultdict
from pathlib import Path

from ..utils.models import ReadyStrategy

SESSION_COMPLETED_MARKER = "COMPLETE_AND_SUBMIT"
SESSION_FAILED_MARKER = "FAILED_AND_SUBMIT"


class TmuxError(RuntimeError):
    pass


class TmuxClient:
    def __init__(self) -> None:
        self._send_locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)

    def create_session(self, session_name: str, workdir: Path, command: list[str]) -> None:
        self._run(
            [
                "new-session",
                "-d",
                "-s",
                session_name,
                "-c",
                str(workdir),
                shlex.join(command),
            ]
        )

    def has_session(self, session_name: str) -> bool:
        result = subprocess.run(
            ["tmux", "has-session", "-t", session_name],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def kill_session(self, session_name: str) -> None:
        if self.has_session(session_name):
            self._run(["kill-session", "-t", session_name])

    def capture_pane(self, session_name: str, start: int = -100) -> str:
        return self._run(
            [
                "capture-pane",
                "-p",
                "-t",
                session_name,
                "-S",
                str(start),
            ]
        )

    def wait_until_ready(self, session_name: str, strategy: ReadyStrategy) -> None:
        deadline = time.monotonic() + strategy.timeout_seconds
        while time.monotonic() < deadline:
            pane = self.capture_pane(session_name, start=-40)
            for substring, response in strategy.auto_respond_patterns:
                if substring in pane:
                    self._run(["send-keys", "-t", session_name, response, "Enter"])
                    time.sleep(strategy.poll_interval_seconds)
                    break
            has_banner = strategy.banner_substring in pane
            has_prompt = self.is_idle_prompt(session_name, strategy.prompt_prefix, pane=pane)
            if has_banner and has_prompt:
                return
            time.sleep(strategy.poll_interval_seconds)
        raise TmuxError(f"timeout waiting for session {session_name!r} to become ready")

    def is_idle_prompt(self, session_name: str, prompt_prefix: str, *, pane: str | None = None) -> bool:
        pane_text = pane if pane is not None else self.capture_pane(session_name, start=-40)
        lines = [line.rstrip() for line in pane_text.splitlines()]
        return any(line.startswith(prompt_prefix) for line in lines)

    def has_active_generation_marker(self, pane: str) -> bool:
        active_markers = (
            "• Working (",
            "• Starting MCP servers",
            "esc to interrupt",
        )
        return any(marker in pane for marker in active_markers)

    @staticmethod
    def terminal_outcome_from_pane(pane: str) -> str | None:
        outcome: str | None = None
        for raw_line in pane.splitlines():
            line = raw_line.strip()
            if line == SESSION_COMPLETED_MARKER:
                outcome = "completed"
            elif line == SESSION_FAILED_MARKER:
                outcome = "failed"
        if outcome is not None:
            return outcome
        return None

    def detect_terminal_outcome(self, session_name: str, *, start: int = -40) -> str | None:
        pane = self.capture_pane(session_name, start=start)
        return self.terminal_outcome_from_pane(pane)

    def wait_for_settled_output(
        self,
        session_name: str,
        *,
        settled_seconds: float,
        poll_interval_seconds: float = 0.2,
    ) -> bool:
        baseline_pane = self.capture_pane(session_name, start=-40)
        observed_activity = False
        unchanged_started_at: float | None = None

        while True:
            if not self.has_session(session_name):
                return False

            pane = self.capture_pane(session_name, start=-40)
            if pane != baseline_pane:
                observed_activity = True

            if not observed_activity:
                time.sleep(poll_interval_seconds)
                continue

            if pane != baseline_pane:
                baseline_pane = pane
                unchanged_started_at = None
                time.sleep(poll_interval_seconds)
                continue

            if self.has_active_generation_marker(pane):
                unchanged_started_at = None
                time.sleep(poll_interval_seconds)
                continue

            if unchanged_started_at is None:
                unchanged_started_at = time.monotonic()
            elif time.monotonic() - unchanged_started_at >= settled_seconds:
                return True

            time.sleep(poll_interval_seconds)

    def paste_and_submit(self, session_name: str, text: str) -> None:
        buffer_name = f"orchestrator-{uuid.uuid4().hex}"
        lock = self._send_locks[session_name]
        with lock:
            self._run(["set-buffer", "-b", buffer_name, "--", text])
            try:
                self._run(["paste-buffer", "-p", "-t", session_name, "-b", buffer_name])
                time.sleep(0.2)
                self._run(["send-keys", "-t", session_name, "Enter"])
            finally:
                self._run(["delete-buffer", "-b", buffer_name], check=False)

    def _run(self, args: list[str], check: bool = True) -> str:
        result = subprocess.run(
            ["tmux", *args],
            capture_output=True,
            text=True,
        )
        if check and result.returncode != 0:
            stderr = result.stderr.strip() or result.stdout.strip()
            raise TmuxError(f"tmux {' '.join(args)} failed: {stderr}")
        return result.stdout
