from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class RoleOverride:
    model: str | None = None
    thinking: str | None = None


@dataclass(frozen=True)
class BabelConfig:
    role_overrides: dict[str, RoleOverride] = field(default_factory=dict)
    agent_runtime: str = "codex"

    @classmethod
    def load(cls, config_path: Path) -> "BabelConfig":
        if not config_path.exists():
            return cls()
        with config_path.open("rb") as f:
            data = tomllib.load(f)
        overrides: dict[str, RoleOverride] = {}
        for role_name, role_data in data.get("roles", {}).items():
            overrides[role_name] = RoleOverride(
                model=role_data.get("model") or None,
                thinking=role_data.get("thinking") or None,
            )
        agent_runtime = data.get("agent_runtime") or "codex"
        return cls(role_overrides=overrides, agent_runtime=agent_runtime)

    def get_role_override(self, role_name: str) -> RoleOverride:
        return self.role_overrides.get(role_name, RoleOverride())
