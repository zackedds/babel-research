from __future__ import annotations

from pathlib import Path

from ..utils.models import RoleDefinition


class RoleRegistryError(ValueError):
    pass


def load_roles(path: Path) -> dict[str, RoleDefinition]:
    if path.is_dir():
        roles: dict[str, RoleDefinition] = {}
        for role_file in sorted(path.glob("*.yaml")):
            if role_file.name.startswith("."):
                continue
            name = role_file.stem
            payload = _parse_role_file(role_file.read_text(encoding="utf-8"))
            prompt = payload.get("prompt", "").strip()
            if not prompt:
                raise RoleRegistryError(f"role {name!r} is missing a prompt")
            roles[name] = RoleDefinition(
                name=name,
                prompt=prompt,
                model=payload.get("model") or None,
                thinking=payload.get("thinking") or None,
            )
        return roles

    raw = path.read_text(encoding="utf-8")
    data = _parse_roles_yaml(raw)
    return _build_roles(data)


def _build_roles(data: dict[str, dict[str, str]]) -> dict[str, RoleDefinition]:
    roles: dict[str, RoleDefinition] = {}
    for name, payload in data.items():
        prompt = payload.get("prompt", "").strip()
        if not prompt:
            raise RoleRegistryError(f"role {name!r} is missing a prompt")
        roles[name] = RoleDefinition(
            name=name,
            prompt=prompt,
            model=payload.get("model") or None,
            thinking=payload.get("thinking") or None,
        )
    return roles


def _parse_role_file(text: str) -> dict[str, str]:
    data = _parse_simple_yaml(text)
    if any(isinstance(value, dict) for value in data.values()):
        raise RoleRegistryError("role files must contain role fields only, not nested role names")
    return data


def _parse_roles_yaml(text: str) -> dict[str, dict[str, str]]:
    roles: dict[str, dict[str, str]] = {}
    current_role: str | None = None
    lines = text.splitlines()
    index = 0

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        index += 1

        if not stripped or stripped.startswith("#"):
            continue

        if not line.startswith(" ") and stripped.endswith(":"):
            current_role = stripped[:-1].strip()
            if not current_role:
                raise RoleRegistryError("empty role name")
            roles[current_role] = {}
            continue

        if current_role is None:
            raise RoleRegistryError(f"unexpected line before any role: {line!r}")

        if not line.startswith("  "):
            raise RoleRegistryError(f"unsupported indentation: {line!r}")

        field_line = line[2:]
        if ":" not in field_line:
            raise RoleRegistryError(f"invalid field line: {line!r}")

        key, value = field_line.split(":", 1)
        key = key.strip()
        value = value.lstrip()

        if value == "|":
            block: list[str] = []
            while index < len(lines):
                block_line = lines[index]
                if block_line.startswith("    "):
                    block.append(block_line[4:])
                    index += 1
                    continue
                if not block_line.strip():
                    block.append("")
                    index += 1
                    continue
                break
            roles[current_role][key] = "\n".join(block).rstrip()
            continue

        roles[current_role][key] = _strip_inline_string(value)

    return roles


def _parse_simple_yaml(text: str) -> dict[str, str]:
    payload: dict[str, str] = {}
    lines = text.splitlines()
    index = 0

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        index += 1

        if not stripped or stripped.startswith("#"):
            continue

        if line.startswith(" "):
            raise RoleRegistryError(f"unsupported indentation: {line!r}")

        if ":" not in line:
            raise RoleRegistryError(f"invalid field line: {line!r}")

        key, value = line.split(":", 1)
        key = key.strip()
        value = value.lstrip()

        if value == "|":
            block: list[str] = []
            while index < len(lines):
                block_line = lines[index]
                if block_line.startswith("  "):
                    block.append(block_line[2:])
                    index += 1
                    continue
                if not block_line.strip():
                    block.append("")
                    index += 1
                    continue
                break
            payload[key] = "\n".join(block).rstrip()
            continue

        payload[key] = _strip_inline_string(value)

    return payload


def _strip_inline_string(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
