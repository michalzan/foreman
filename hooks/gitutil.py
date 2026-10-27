"""Small git queries the gates need. Every function tolerates an empty repo."""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


# Seconds a git query may take; None waits. The guard sets one below its own hook timeout, so a hung git
# crashes it (and it fails closed) instead of the hook timing out (which lets the call through).
TIMEOUT: float | None = None


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "core.quotepath=off", *args],
        cwd=root, check=True, capture_output=True, text=True, timeout=TIMEOUT,
    ).stdout


def head(root: Path) -> str:
    try:
        return git(root, "rev-parse", "--verify", "-q", "HEAD").strip()
    except subprocess.CalledProcessError:
        return ""


def current_branch(root: Path) -> str:
    try:
        return git(root, "symbolic-ref", "-q", "--short", "HEAD").strip() if head(root) else ""
    except subprocess.CalledProcessError:
        return ""


def is_toplevel(root: Path) -> bool:
    """True when root is the top of a git work tree. Paths from git are relative to that top."""
    try:
        return Path(git(root, "rev-parse", "--show-toplevel").strip()).resolve() == root.resolve()
    except (subprocess.CalledProcessError, OSError):
        return False


def dirty_files(root: Path) -> list[str]:
    """Paths that differ from HEAD, including untracked ones. Ignored files are excluded."""
    parts = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all").split("\0")
    names, i = [], 0
    while i < len(parts):
        entry = parts[i]
        if len(entry) > 3:
            names.append(entry[3:])
            if entry[0] in "RC":
                i += 1  # a rename or copy is followed by its old path
        i += 1
    return sorted(names)


def changed_files(root: Path, since: str) -> list[str]:
    """Files committed after `since`, plus everything dirty. An unknown `since` counts as none."""
    committed: list[str] = []
    if since and since != head(root):
        try:
            committed = git(root, "diff", "--name-only", "-z", since, "HEAD").split("\0")
        except subprocess.CalledProcessError:
            committed = []
    return sorted({f for f in [*committed, *dirty_files(root)] if f})


def fingerprint(root: Path) -> str:
    """Identity of the commit plus the content of every dirty file."""
    h = hashlib.sha256(head(root).encode())
    for name in dirty_files(root):
        path = root / name
        h.update(name.encode() + b"\0")
        h.update(hashlib.sha256(path.read_bytes()).digest() if path.is_file() else b"<gone>")
    return h.hexdigest()
