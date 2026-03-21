from __future__ import annotations

from pathlib import Path

from ..utils.models import ReadyStrategy, RuntimeDefinition


class RuntimeRegistryError(ValueError):
    pass


def builtin_runtimes() -> dict[str, RuntimeDefinition]:
    return {
        "codex": RuntimeDefinition(
            name="codex",
            startup_command=["codex", "--dangerously-bypass-approvals-and-sandbox", "--no-alt-screen"],
            ready_strategy=ReadyStrategy(
                banner_substring="OpenAI Codex",
                prompt_prefix="› ",
                timeout_seconds=30.0,
                auto_respond_patterns=(
                    ("Update available", "2"),
                    ("Choose how you'd like Codex to proceed.", "2"),
                ),
            ),
            agent_logs_dir=Path.home() / ".codex" / "sessions",
        ),
        "claude-code": RuntimeDefinition(
            name="claude-code",
            startup_command=["claude", "--dangerously-skip-permissions"],
            ready_strategy=ReadyStrategy(
                banner_substring="Claude Code",
                prompt_prefix="❯",
                timeout_seconds=45.0,
                auto_respond_patterns=(
                    ("Yes, I trust this folder", ""),
                ),
            ),
            agent_logs_dir=Path.home() / ".claude" / "projects",
            model_flag="--model",
            pre_paste_delay_seconds=5.0,
        ),
    }


def get_runtime(name: str) -> RuntimeDefinition:
    try:
        return builtin_runtimes()[name]
    except KeyError as exc:
        raise RuntimeRegistryError(f"unknown runtime: {name}") from exc
