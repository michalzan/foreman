"""Read a hook's JSON input from stdin and write its JSON output to stdout."""
import json
import sys


def read_input() -> dict:
    raw = sys.stdin.read()
    return json.loads(raw) if raw.strip() else {}


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj))
