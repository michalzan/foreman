"""Read a hook's JSON input from stdin and write its JSON output to stdout."""
import hashlib
import json
import re
import sys
from pathlib import Path

HOOKS_JSON = Path(__file__).resolve().parent / "hooks.json"
STALE = ("Claude Code loaded Foreman's hooks before they last changed, so some may not run (the unit gate did not, "
         "in one pilot run): quit claude and start it again; /clear is not enough")


def read_input() -> dict:
    raw = sys.stdin.read()
    return json.loads(raw) if raw.strip() else {}


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj))


def stamp(data: dict | None = None) -> str:
    """Digest of the hook registrations: event, matcher, script and timeout. Every command in hooks.json passes
    it to its script, so a script can tell when the process that called it loaded an older hooks.json."""
    if data is None:
        data = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    rows = sorted((event, g.get("matcher", ""), m.group(1) if m else h.get("command", ""), h.get("timeout"))
                  for event, groups in data.get("hooks", {}).items() for g in groups for h in g.get("hooks", [])
                  for m in [re.search(r"(\w+\.py)", h.get("command", ""))])
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()[:12]


def stale(argv: list[str]) -> bool:
    """True when the caller's registrations differ from hooks.json on disk; those from before the stamp pass none."""
    try:
        return argv[:1] != [stamp()]
    except (OSError, ValueError):
        return False  # hooks.json unreadable: the doctor reports a broken plugin
