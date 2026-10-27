"""Remove the harness as install.py put it into ~/.claude, before it became a plugin. Safe to re-run.

The plugin itself is removed with `claude plugin uninstall foreman@michalzan`. This script only cleans up
the older copy: hooks in ~/.claude/hooks/harness, their entries in settings.json, the harness-init and
harness-run skills, and the run contract in ~/.claude/CLAUDE.md. The trust store
(~/.claude/harness-trusted.json) and each project's .harness.toml, decisions log and parked file stay:
they are the user's, not the harness's.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

MARK_START = "<!-- harness:run-contract:start -->"
MARK_END = "<!-- harness:run-contract:end -->"
OURS = "hooks/harness/"
SKILLS = ("harness-init", "harness-run")


def _read_settings(path: Path) -> tuple[dict, str]:
    """Current settings and their exact text. Refuses to continue on a file it cannot parse."""
    if not path.is_file():
        return {}, ""
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except ValueError as e:
        raise SystemExit(f"{path} is not valid JSON ({e}). Fix it and re-run; nothing was changed.")
    if not isinstance(data, dict):
        raise SystemExit(f"{path} is not a JSON object. Fix it and re-run; nothing was changed.")
    return data, text


def uninstall(home: Path) -> None:
    claude = home / ".claude"
    settings_path = claude / "settings.json"
    settings, old_text = _read_settings(settings_path)  # refuses before anything is removed
    hooks = settings.get("hooks", {})
    for event in list(hooks):
        kept = [g for g in hooks[event] if OURS not in json.dumps(g)]
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if "hooks" in settings and not hooks:
        del settings["hooks"]
    new_text = json.dumps(settings, indent=2, ensure_ascii=False) + "\n"
    if old_text and json.loads(new_text) != json.loads(old_text):
        (claude / f"settings.json.harness-backup-{time.strftime('%Y%m%d-%H%M%S')}").write_text(old_text, encoding="utf-8")
        tmp = claude / "settings.json.harness-tmp"
        tmp.write_text(new_text, encoding="utf-8")
        tmp.replace(settings_path)

    shutil.rmtree(claude / "hooks" / "harness", ignore_errors=True)
    for name in SKILLS:
        shutil.rmtree(claude / "skills" / name, ignore_errors=True)

    memory = claude / "CLAUDE.md"
    if memory.is_file():
        text = memory.read_text(encoding="utf-8")
        cleaned = re.sub(r"\n*" + re.escape(MARK_START) + ".*?" + re.escape(MARK_END) + r"\n?", "\n", text, flags=re.S)
        if cleaned != text:
            memory.write_text(cleaned.rstrip("\n") + "\n" if cleaned.strip() else "", encoding="utf-8")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="uninstall.py")
    ap.add_argument("--home", type=Path, default=Path.home())
    args = ap.parse_args(argv)
    uninstall(args.home)
    print(f"old harness install removed from {args.home / '.claude'}; your approvals in harness-trusted.json were kept")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
