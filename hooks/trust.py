"""Which project declarations the user has approved.

A declaration names a shell command the stop gate runs. One that arrives with a cloned
repo, or changes in a pull, must not run until the user has read and approved it.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import declaration


def store_path() -> Path:
    return Path.home() / ".claude" / "foreman-trusted.json"


def digest(root: Path) -> str:
    try:
        return hashlib.sha256((root / declaration.NAME).read_bytes()).hexdigest()
    except OSError:
        return ""


def _load() -> dict:
    try:
        data = json.loads(store_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def is_trusted(root: Path) -> bool:
    d = digest(root)
    return bool(d) and _load().get(str(root.resolve())) == d


def approve(root: Path) -> None:
    data = _load()
    data[str(root.resolve())] = digest(root)
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def main(argv: list[str]) -> int:
    root = declaration.find_root(Path.cwd())
    if root is None:
        print(f"no {declaration.NAME} found here or above", file=sys.stderr)
        return 1
    if argv[:1] == ["approve"]:
        approve(root)
        print(f"{root / declaration.NAME}: approved")
    else:
        print(f"{root / declaration.NAME}: " + ("approved" if is_trusted(root) else "not approved"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
