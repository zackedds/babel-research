from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class RoleDefinition:
    name: str
    prompt: str
    model: str | None = None
    thinking: str | None = None


@dataclass(frozen=True)
class ReadyStrategy:
    banner_substring: str
    prompt_prefix: str
    timeout_seconds: float = 20.0
    poll_interval_seconds: float = 0.2
    auto_respond_patterns: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class RuntimeDefinition:
    name: str
    startup_command: list[str]
    ready_strategy: ReadyStrategy
    agent_logs_dir: Path | None = None


@dataclass(frozen=True)
class AgentSessionSpec:
    role: str
    runtime: str
    session_prompt: str
    workdir: Path
    session_name: str | None = None
    template_vars: dict[str, object] | None = None
    round_index: int | None = None
    task_id: str | None = None
    task_title: str | None = None
    task_description: str | None = None
    session_id: str | None = None
    worktree_path: Path | None = None


@dataclass(frozen=True)
class AgentSession:
    id: str
    tmux_session: str
    role: str
    runtime: str
    workdir: Path
    full_prompt: str
    status: str
    started_at: datetime
    terminal_outcome: str | None = None
    round_index: int | None = None
    task_id: str | None = None
    task_title: str | None = None
    task_description: str | None = None
    finished_at: datetime | None = None
    failure_reason: str | None = None
    failure_source: Literal["launch", "watcher", "tmux_missing", "driver", "unknown"] | None = None
    last_observed_at: datetime | None = None
    agent_log_file: Path | None = None
    worktree_path: Path | None = None
    wiki_file_paths: tuple[str, ...] | None = None


@dataclass(frozen=True)
class OrchestrationRun:
    id: str
    status: Literal["running", "completed", "failed"]
    initial_prompt: str
    workdir: Path
    max_rounds: int | None
    max_workers: int | None
    rounds_completed: int
    planner_session_ids: list[str]
    worker_waves: list[list[str]]
    librarian_session_ids: list[str]
    claimed_task_ids: list[str]
    current_phase: Literal["planner", "worker", "librarian", "stopped"]
    created_at: datetime
    updated_at: datetime
