"""Move a project from the harness file names to Foreman's. Safe to re-run.

    .harness.toml -> .foreman.toml   (with git mv when tracked)
    .harness/     -> .foreman/       (run state, gitignored)
    .gitignore:      .harness/ -> .foreman/

The approval does not move with the file: approve .foreman.toml afterwards, as after any change to it.
usage: uv run --python 3.12 migrate.py [project directory, default: here]
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

OLD, NEW = ".harness.toml", ".foreman.toml"
OLD_DIR, NEW_DIR = ".harness", ".foreman"
TRUST = Path(__file__).resolve().parent / "hooks" / "trust.py"


def _root(start: Path) -> Path | None:
    start = start.resolve()
    return next((d for d in (start, *start.parents) if (d / OLD).is_file() or (d / NEW).is_file()), None)


def _tracked(root: Path, name: str) -> bool:
    return subprocess.run(["git", "ls-files", "--error-unmatch", name], cwd=root,
                          capture_output=True).returncode == 0


def _ignore(root: Path) -> None:
    path = root / ".gitignore"
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    renamed = [NEW_DIR + "/" if line.strip() in (OLD_DIR, OLD_DIR + "/") else line for line in lines]
    if NEW_DIR + "/" not in renamed:
        renamed.append(NEW_DIR + "/")
    path.write_text("\n".join(renamed) + "\n", encoding="utf-8")


def migrate(start: Path) -> str:
    root = _root(start)
    if root is None or not (root / OLD).is_file():
        return f"nothing to migrate: no {OLD} in {start} or above"
    if (root / NEW).exists():
        raise SystemExit(f"both {OLD} and {NEW} exist in {root}: keep one by hand, then re-run")
    if (root / OLD_DIR / "run.json").is_file():
        raise SystemExit(f"a run is active in {root}: finish it (or run.py stop) before migrating")
    for name in (".gitignore", OLD_DIR):
        if (root / name).is_symlink():
            raise SystemExit(f"{root / name} is a symlink: migrate.py does not write through links. Replace it, then re-run")
    if (root / NEW_DIR).exists():
        raise SystemExit(f"{NEW_DIR}/ already exists in {root}: remove it or move it aside, then re-run")
    if _tracked(root, OLD):
        subprocess.run(["git", "mv", OLD, NEW], cwd=root, check=True, capture_output=True)
    else:
        (root / OLD).rename(root / NEW)
    if (root / OLD_DIR).is_dir():
        (root / OLD_DIR).rename(root / NEW_DIR)
    _ignore(root)
    return (f"{root}: {OLD} -> {NEW}, {OLD_DIR}/ -> {NEW_DIR}/, .gitignore updated. Commit the rename, then read "
            f"{NEW} and approve it:\n  uv run --no-project --python 3.12 \"{TRUST}\" approve")


def main(argv: list[str]) -> int:
    print(migrate(Path(argv[0]) if argv else Path.cwd()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
