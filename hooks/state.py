"""Per-project Foreman state under .foreman/ (gitignored)."""
from __future__ import annotations

import errno
import hashlib
import json
import os
import time
from pathlib import Path


class State:
    def __init__(self, root: Path):
        self.dir = root / ".foreman"
        self.walkthrough = self.dir / "walkthrough"

    def _read(self, name: str) -> dict:
        try:
            data = json.loads((self.dir / name).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _open(self, name: str, flags: int) -> int:
        """A file descriptor in the state directory, refusing symlinks on the way: a repository could otherwise
        point the gates' writes anywhere, and they write even while its declaration is not approved."""
        self.dir.mkdir(exist_ok=True)
        if self.dir.is_symlink():
            raise OSError(errno.ELOOP, "the Foreman state directory is a symlink", str(self.dir))
        return os.open(self.dir / name, flags | os.O_WRONLY | os.O_NOFOLLOW, 0o644)

    def _write(self, name: str, data: dict) -> None:
        tmp = self.dir / (name + ".tmp")
        if tmp.is_symlink():
            tmp.unlink()
        with os.fdopen(self._open(tmp.name, os.O_CREAT | os.O_TRUNC), "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=2))
        tmp.replace(self.dir / name)  # replaces a symlink at the destination, never writes through it

    def read(self) -> dict:
        return self._read("state.json")

    def write(self, data: dict) -> None:
        self._write("state.json", data)

    def run(self) -> dict | None:
        return self._read("run.json") or None

    def start_run(self, plans: list[tuple[str, list[str]]], start_head: str, plain: bool = False) -> None:
        """A run is one plan, or a chain of plans worked through in order."""
        self._write("run.json", {
            "plans": [{"file": f, "units": list(u)} for f, u in plans],
            "units": [unit for _, u in plans for unit in u],
            "start_head": start_head, "session_id": "", "plain": plain,
            "plan_digests": {f: self.plan_digest(f) for f, _ in plans},
        })
        self.log(step="run", action="start", units=sum(len(u) for _, u in plans), head=start_head)

    @staticmethod
    def plan_files(run: dict) -> list[str]:
        plans = run.get("plans")
        return [p["file"] for p in plans] if plans else [run["plan_file"]]  # a run from before chains

    def plan_digest(self, plan_file: str) -> str:
        """Content hash of the plan, so a moved resume point shows even when git ignores the file."""
        try:
            return hashlib.sha256((self.dir.parent / plan_file).read_bytes()).hexdigest()
        except OSError:
            return ""

    def plan_passed(self) -> None:
        run = self.run()
        if run:
            run["plan_digests"] = {f: self.plan_digest(f) for f in self.plan_files(run)}
            self._write("run.json", run)

    def bind_run(self, session_id: str) -> None:
        run = self.run()
        if run and not run.get("session_id"):
            run["session_id"] = session_id
            self._write("run.json", run)

    def session_started(self, session_id: str, head: str) -> None:
        """Where a session began, so the gates charge it only for what it did (the last 20 sessions are kept).
        The first start counts: SessionStart fires again after compaction and on resume, with the same id."""
        s = self.read()
        sessions = dict(s.get("sessions") or {})
        if session_id in sessions:
            return
        sessions[session_id] = head
        s["sessions"] = dict(list(sessions.items())[-20:])
        self.write(s)

    def session_head(self, session_id: str) -> str:
        return str((self.read().get("sessions") or {}).get(session_id, ""))

    def end_run(self, how: str = "") -> None:
        """how: complete (the session said so and the judge agreed) or stopped (run.py stop)."""
        if self.run():
            self.log(step="run", action="end", how=how)
        (self.dir / "run.json").unlink(missing_ok=True)

    def log(self, **event) -> None:
        event = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **event}
        with os.fdopen(self._open("gate.log", os.O_CREAT | os.O_APPEND), "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
