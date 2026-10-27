"""Verify that a project and the Foreman plugin are intact. Never blocks."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import audit
import declaration
import gitutil
import hookio
import trust
import tunnels
from state import State

HOOKS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = HOOKS_DIR.parent
REQUIRED_HOOKS = [("SessionStart", "doctor.py"), ("PreToolUse", "guard.py"), ("PreToolUse", "unit_gate.py"),
                  ("Stop", "stop_gate.py")]
# Where Foreman lived before it was a plugin and was still called the harness (install.py, until 2026-10-26).
LEGACY = "hooks/harness/"
STOP_HOOK_TIMEOUT = 1200


def _json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def findings(cwd: Path, home: Path, plugin_root: Path = PLUGIN_ROOT) -> list[str]:
    root = declaration.find_root(cwd)
    if root is None:
        old = next((d for d in (cwd.resolve(), *cwd.resolve().parents) if (d / declaration.OLD_NAME).is_file()), None)
        if old is not None:
            return [f"this project uses Foreman's old file names ({declaration.OLD_NAME}, .harness/): gates are off. "
                    f'Move them with: uv run --no-project --python 3.12 "{PLUGIN_ROOT}/migrate.py" "{old}" '
                    f"and then approve {declaration.NAME}"]
        return [f"no {declaration.NAME} here: gates are off for this project"]
    if not trust.is_trusted(root):
        return [f"{declaration.NAME} is new or changed and not approved: gates are off. Read it, then run: "
                f'uv run --no-project --python 3.12 "{HOOKS_DIR}/trust.py" approve']
    if not gitutil.is_toplevel(root):
        return [f"{declaration.NAME} is not at the git top level: gates are off. Move it to the top of the repository."]
    try:
        decl = declaration.load(root)
    except declaration.DeclarationError as e:
        return [f"declaration unreadable, gates are off: {e}"]
    out: list[str] = []

    for name in ("settings.json", "settings.local.json"):
        if _json(root / ".claude" / name).get("disableAllHooks") is True:
            out.append(f".claude/{name} sets disableAllHooks: every gate is off in this project")
    user = _json(home / ".claude" / "settings.json")
    if user.get("disableAllHooks") is True:
        out.append("~/.claude/settings.json sets disableAllHooks: every gate is off")
    registered = _json(plugin_root / "hooks" / "hooks.json").get("hooks", {})
    for event, script in REQUIRED_HOOKS:
        if script not in json.dumps(registered.get(event, [])):
            out.append(f"{event} hook missing from the plugin's hooks/hooks.json ({script})")
        elif not (plugin_root / "hooks" / script).is_file():
            out.append(f"{script} is missing from the plugin: reinstall it")
    if (home / ".claude" / LEGACY).exists() or LEGACY in json.dumps(user.get("hooks", {})):
        out.append("an old install (from when Foreman was called the harness) is still in ~/.claude (hooks/harness or "
                   "settings.json) and runs beside the plugin: remove it with uninstall.py from the Foreman repository")

    if not decl.audit_fingerprint:
        out.append("no audit recorded: run /foreman:init")
    elif audit.fingerprint(root, decl.audit_files) != decl.audit_fingerprint:
        out.append(f"audit is stale (build files changed since {decl.audit_date}): "
                   + ("re-run /foreman:init after the run, not during it; put it in the hand-back"
                      if State(root).run() else "re-run /foreman:init"))
    for rel in (decl.decisions_log, decl.parked):
        if not (root / rel).is_file():
            out.append(f"missing {rel}")
        elif not declaration.stays_inside(root, rel):
            out.append(f"{rel} is a symlink or leaves the project: the gate will not write to it")
    ignored = subprocess.run(["git", "check-ignore", "-q", ".foreman/x"], cwd=root)
    if ignored.returncode != 0:
        out.append(".foreman/ is not gitignored")
    if decl.check_timeout > STOP_HOOK_TIMEOUT - 100:
        out.append(f"check timeout {decl.check_timeout}s is too close to the Stop hook's {STOP_HOOK_TIMEOUT}s limit")

    run = State(root).run()
    if run:
        out.append(run_line(run))
        out.append(f"continue from the resume point in {', then '.join(State.plan_files(run))}; "
                   f"decisions so far are in {decl.decisions_log}, parked questions in {decl.parked}")
        held = tunnels.warning()
        if held:
            out.append(held)
    return out


def contract() -> str:
    """The run contract. It reaches the session through run.py start and, during a run, every session start."""
    try:
        return (PLUGIN_ROOT / "run-contract.md").read_text(encoding="utf-8").strip()
    except OSError:
        return "## Run contract\n\n(run-contract.md is missing from the plugin: reinstall it)"


def run_line(run: dict) -> str:
    plans = run.get("plans") or [{"file": run["plan_file"], "units": run["units"]}]
    parts = [f"{', '.join(p['units'])} ({p['file']})" for p in plans]
    return ("run active: " + "; then ".join(parts)
            + (", plain: skip the simplify pass" if run.get("plain") else ""))


def main(argv: list[str]) -> int:
    if os.environ.get("FOREMAN_JUDGE"):
        return 0
    cli = "--cli" in argv
    try:
        cwd = Path.cwd() if cli else Path(hookio.read_input().get("cwd") or os.getcwd())
        found = findings(cwd, Path.home())
    except Exception as e:  # never block a session start
        found = [f"doctor crashed: {e!r}"]
    if cli:
        print("\n".join(found) or "foreman: ok")
    elif found:
        text = "foreman doctor:\n" + "\n".join(f"- {f}" for f in found)
        context = text + ("\n\n" + contract() if any(f.startswith("run active") for f in found) else "")
        hookio.emit({"systemMessage": text,
                     "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}})
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
