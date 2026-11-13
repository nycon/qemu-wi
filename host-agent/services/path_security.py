"""
Path and identifier hardening for the host agent.

All user-influenced filesystem paths must resolve under an allowlisted root.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from agent.config import agent_settings

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def allowed_roots() -> list[Path]:
    """Filesystem roots the agent may read/write for VM/image/backup ops."""
    roots = [
        Path(agent_settings.STORAGE_BASE_PATH).resolve(),
        Path(agent_settings.CLOUD_IMAGES_PATH).resolve(),
        Path(agent_settings.CLOUD_INIT_PATH).resolve(),
        Path("/var/lib/qemu-interface").resolve(),
    ]
    # Deduplicate while preserving order
    seen: set[str] = set()
    out: list[Path] = []
    for root in roots:
        key = str(root)
        if key not in seen:
            seen.add(key)
            out.append(root)
    return out


def assert_safe_id(value: str, *, field: str = "id") -> str:
    """Reject path traversal and odd characters in IDs / domain fragments."""
    if not value or not _SAFE_ID_RE.fullmatch(value):
        raise ValueError(f"Invalid {field}")
    if ".." in value or "/" in value or "\\" in value:
        raise ValueError(f"Invalid {field}")
    return value


def assert_safe_name(value: str, *, field: str = "name") -> str:
    if not value or not _SAFE_NAME_RE.fullmatch(value):
        raise ValueError(f"Invalid {field}")
    if ".." in value:
        raise ValueError(f"Invalid {field}")
    return value


def resolve_under_allowlist(path: str | Path, *, field: str = "path") -> Path:
    """Resolve path and require it to stay under an allowlisted root."""
    if path is None or str(path).strip() == "":
        raise ValueError(f"Missing {field}")
    resolved = Path(path).resolve()
    for root in allowed_roots():
        try:
            resolved.relative_to(root)
            return resolved
        except ValueError:
            continue
    raise ValueError(f"{field} escapes allowed storage roots: {resolved}")


def resolve_under_root(path: str | Path, root: str | Path, *, field: str = "path") -> Path:
    """Resolve path and require it under a specific root."""
    root_r = Path(root).resolve()
    resolved = Path(path).resolve()
    try:
        resolved.relative_to(root_r)
    except ValueError as exc:
        raise ValueError(f"{field} must be under {root_r}") from exc
    return resolved


def join_under(root: str | Path, *parts: str, field: str = "path") -> Path:
    """Join parts under root and verify the result cannot escape."""
    root_r = Path(root).resolve()
    for part in parts:
        assert_safe_id(part, field=field)
    candidate = root_r.joinpath(*parts)
    return resolve_under_root(candidate, root_r, field=field)


def is_under_allowlist(path: str | Path) -> bool:
    try:
        resolve_under_allowlist(path)
        return True
    except Exception:
        return False
