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
            ),
            agent_logs_dir=Path.home() / ".codex" / "sessions",
        )
    }


def get_runtime(name: str) -> RuntimeDefinition:
    try:
        return builtin_runtimes()[name]
    except KeyError as exc:
        raise RuntimeRegistryError(f"unknown runtime: {name}") from exc
