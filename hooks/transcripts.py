"""Read Claude Code's transcripts of one project inside a time window, as a list of events.

Claude Code keeps a project's sessions in ~/.claude/projects/<the project path with every character other
than a letter or digit replaced by ->/<session id>.jsonl, and each session's subagents in
<session id>/subagents/*.jsonl. Transcript timestamps are UTC; Foreman's gate log is local time.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

DENIED = ("The user doesn't want to proceed", "Permission for this action was denied")
REPORT = re.compile(r"<agent-message from=|^<task-notification>")


@dataclass
class Entry:
    file: Path
    line: int
    sub: bool
    data: dict
    ts: str


@dataclass
class Event:
    ts: str
    kind: str  # stop, block, launch, report, user, hook, bash, monitor, denied, error
    text: str
    entry: Entry | None = None
    command: str = ""
    background: bool = False
    seconds: float | None = None
    cwd: str = ""
    sub: bool = False
    flags: list[str] = field(default_factory=list)


def project_dir(root: Path, base: Path | None = None) -> Path:
    base = base or Path.home() / ".claude" / "projects"
    return base / re.sub(r"[^A-Za-z0-9]", "-", str(root.resolve()))


def project_dirs(root: Path, base: Path | None = None) -> list[Path]:
    """The folders that can hold this project's sessions: its own, and those of sessions started in one of
    its subfolders (named with the project's name as a prefix). read_window keeps only sessions whose
    working directory is inside the project, so a sibling repository with a longer name is left out."""
    own = project_dir(root, base)
    if not own.parent.is_dir():
        return []
    return sorted(d for d in own.parent.iterdir() if d.is_dir() and (d == own or d.name.startswith(own.name + "-")))


def to_utc(local: str) -> str:
    """A gate-log time (local, no zone) as a transcript time (UTC, Z)."""
    return datetime.fromisoformat(local).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def seconds_between(a: str, b: str) -> float:
    parse = lambda t: datetime.fromisoformat(t.replace("Z", "+00:00"))
    return (parse(b) - parse(a)).total_seconds()


def read_window(folders: list[Path], start: str, end: str, root: Path | None = None) -> tuple[list[Entry], int]:
    """Entries of every session and subagent transcript with start <= timestamp <= end (UTC), in time order,
    and the number of lines that were not JSON. With root, only sessions started inside it."""
    entries: list[Entry] = []
    broken = 0
    for session in sorted(s for folder in folders for s in folder.glob("*.jsonl")):
        project = session.parent
        if _headless(session) or (root is not None and not _inside(session, root)):
            continue
        files = [(session, False)] + [(s, True) for s in sorted((project / session.stem / "subagents").glob("*.jsonl"))]
        for path, sub in files:
            # split on "\n" only: str.splitlines() also breaks on U+2028, U+2029 and NEL, which a message may hold raw
            for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").split("\n")):
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except ValueError:
                    broken += 1
                    continue
                ts = data.get("timestamp") if isinstance(data, dict) else None
                if isinstance(ts, str) and start <= ts <= end:
                    entries.append(Entry(path, n, sub, data, ts))
    entries.sort(key=lambda e: e.ts)
    return entries, broken


def _inside(session: Path, root: Path) -> bool:
    """Whether the session's first recorded working directory is the project or below it."""
    top = str(root.resolve())
    cwd = _first(session, "cwd")
    return cwd == top or cwd.startswith(top + "/")


def _headless(session: Path) -> bool:
    """Sessions started by a program (a security review, Foreman's judge) rather than a person: their
    entrypoint is sdk-*. They are not part of the run."""
    return _first(session, "entrypoint").startswith("sdk")


def _first(session: Path, key: str) -> str:
    """The first value of key in the session; a line that merely mentions the key is passed over."""
    with session.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            if f'"{key}"' in line:
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                if isinstance(data, dict) and key in data:
                    return str(data[key])
    return ""


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(_text(c.get("text", c.get("content", ""))) for c in content if isinstance(c, dict))
    return ""


def events(entries: list[Entry]) -> list[Event]:
    out: list[Event] = []
    last_text: dict[Path, str] = {}
    calls: dict[str, Event] = {}
    blocks: dict[Path, str] = {}  # the form this turn's block came in

    def block(e: Entry, text: str, form: str) -> None:
        """A blocked stop is written twice, as a feedback message and as an attachment: the first one counts."""
        if blocks.setdefault(e.file, form) == form:
            out.append(Event(e.ts, "block", text, e))

    def said(e: Entry, text: str) -> None:
        if REPORT.search(text):
            out.append(Event(e.ts, "report", text[:200], e))
        elif text.startswith("Stop hook feedback:"):
            block(e, text.split("\n", 1)[-1].strip(), "feedback")
        elif not e.data.get("isMeta") and not text.startswith("<"):
            out.append(Event(e.ts, "user", text, e))

    for e in entries:
        d, kind = e.data, e.data.get("type")
        msg = d.get("message") if isinstance(d.get("message"), dict) else {}
        content = msg.get("content")
        if kind == "assistant":
            blocks.pop(e.file, None)
            for b in content if isinstance(content, list) else []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and _text(b.get("text")).strip():
                    last_text[e.file] = _text(b.get("text"))
                elif b.get("type") == "tool_use":
                    name, inp = b.get("name"), b.get("input") if isinstance(b.get("input"), dict) else {}
                    ev = None
                    if name == "Bash":
                        ev = Event(e.ts, "bash", str(inp.get("description", "")), e, command=str(inp.get("command", "")),
                                   background=bool(inp.get("run_in_background")), cwd=str(d.get("cwd", "")), sub=e.sub)
                    elif name == "Monitor":
                        ev = Event(e.ts, "monitor", json.dumps(inp)[:200], e, sub=e.sub)
                    elif name in ("Agent", "Task") and not e.sub:
                        ev = Event(e.ts, "launch", str(inp.get("description", "")), e)
                    if ev:
                        out.append(ev)
                        calls[b.get("id", "")] = ev
        elif kind == "user":
            if isinstance(content, str) and not e.sub:
                said(e, content)
            for b in content if isinstance(content, list) else []:
                if not (isinstance(b, dict) and b.get("type") == "tool_result"):
                    continue
                call = calls.get(b.get("tool_use_id", ""))
                if call and call.kind == "bash":
                    call.seconds = seconds_between(call.ts, e.ts)
                if b.get("is_error"):
                    text = _text(b.get("content"))
                    out.append(Event(e.ts, "denied" if text.startswith(DENIED) else "error", text[:300], e,
                                     command=call.command if call else "", sub=e.sub))
        elif kind == "system" and d.get("subtype") == "stop_hook_summary" and not e.sub:
            out.append(Event(e.ts, "stop", last_text.get(e.file, "").strip(), e))
        elif kind == "attachment":
            a = d.get("attachment") if isinstance(d.get("attachment"), dict) else {}
            if a.get("type") == "queued_command":  # a report or a message that arrived while the session was busy
                if not e.sub:
                    said(e, _text(a.get("prompt")))
            elif a.get("type") == "hook_blocking_error" and a.get("hookEvent") == "Stop":
                err = a.get("blockingError")
                block(e, str(err.get("blockingError", "") if isinstance(err, dict) else err), "attachment")
            elif a.get("type") == "hook_system_message" and "foreman" in str(a.get("content", "")).lower():
                out.append(Event(e.ts, "hook", str(a.get("content", "")), e))
    _read_after(out, entries)
    return out


def _read_after(out: list[Event], entries: list[Entry]) -> None:
    """For each subagent report: seconds until the main session's next assistant turn."""
    main = [e.ts for e in entries if e.data.get("type") == "assistant" and not e.sub]
    for ev in out:
        if ev.kind == "report":
            nxt = next((t for t in main if t > ev.ts), None)
            ev.seconds = seconds_between(ev.ts, nxt) if nxt else None
