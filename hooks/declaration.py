"""Find and load a project's .foreman.toml."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

NAME = ".foreman.toml"
OLD_NAME = ".harness.toml"  # before 2026-10-27, when Foreman was still called the harness


class DeclarationError(Exception):
    pass


@dataclass(frozen=True)
class Declaration:
    root: Path
    check_command: str
    check_timeout: int
    protected: tuple[str, ...]
    frontend_paths: tuple[str, ...]
    decisions_log: str
    parked: str
    audit_date: str
    audit_fingerprint: str
    audit_files: tuple[str, ...]


def _inside(value: object) -> str:
    """A declared path must be relative with no '..' step.

    This depends only on the declaration's own text, which the trust hash covers. Symlinks
    are checked where a path is used (stop_gate._park, the doctor): checking them here would
    let a session switch the gate off by swapping a file it is allowed to edit.
    """
    path = Path(str(value))
    if not str(value).strip() or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"path must be relative and inside the project: {value!r}")
    return str(value)


def _strings(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"{name} must be a list of strings")
    return tuple(value)


def stays_inside(root: Path, rel: str) -> bool:
    """True when root/rel is not a symlink and resolves to a location inside root."""
    target = root / rel
    real_root = root.resolve()
    return not target.is_symlink() and real_root in target.resolve().parents


def find_root(start: Path) -> Path | None:
    start = start.resolve()
    for d in (start, *start.parents):
        if (d / NAME).is_file():
            return d
    return None


def load(root: Path) -> Declaration:
    try:
        data = tomllib.loads((root / NAME).read_text(encoding="utf-8"))
        check, branches, plan = data["check"], data["branches"], data["plan"]
        audit = data.get("audit", {})
        command = str(check["command"])
        if not command.strip():
            raise ValueError("check.command is empty")
        return Declaration(
            root=root,
            check_command=command,
            check_timeout=int(check.get("timeout_seconds", 900)),
            protected=_strings(branches.get("protected", []), "branches.protected"),
            frontend_paths=_strings(data.get("frontend", {}).get("paths", []), "frontend.paths"),
            decisions_log=_inside(plan["decisions_log"]),
            parked=_inside(plan["parked"]),
            audit_date=str(audit.get("date", "")),
            audit_fingerprint=str(audit.get("fingerprint", "")),
            audit_files=tuple(_inside(f) for f in _strings(audit.get("files", []), "audit.files")),
        )
    except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError, ValueError) as e:
        raise DeclarationError(f"{NAME}: {e!r}") from e
