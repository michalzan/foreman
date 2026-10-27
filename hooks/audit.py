"""Fingerprint of the build files an init audit read. A change means the audit is stale."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path


def fingerprint(root: Path, files) -> str:
    h = hashlib.sha256()
    for name in sorted(files):
        path = root / name
        h.update(name.encode() + b"\0")
        h.update(path.read_bytes() if path.is_file() else b"<missing>")
        h.update(b"\0")
    return "sha256:" + h.hexdigest()


if __name__ == "__main__":
    print(fingerprint(Path.cwd(), sys.argv[1:]))
