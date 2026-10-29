"""The hook registrations a Claude Code process loaded, against the ones on disk.

Claude Code reads hooks.json once, when the process starts; /clear starts a new session in the same process.
The scripts are read fresh on every call. In the A6 pilot run (2026-10-28) the process predated the unit
gate: the stop gate ran in its new form, the unit gate never ran, and nothing said so.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import hookio

HOOKS = Path(__file__).resolve().parent.parent / "hooks"


def test_every_hook_command_carries_the_current_stamp():
    registered = json.loads((HOOKS / "hooks.json").read_text())["hooks"]
    commands = [h["command"] for groups in registered.values() for g in groups for h in g["hooks"]]
    assert commands and all(c.endswith(f".py {hookio.stamp()}") for c in commands), (
        f"hooks.json changed: put {hookio.stamp()} at the end of every hook command")


def test_the_stamp_follows_the_registrations_not_the_wording():
    data = json.loads((HOOKS / "hooks.json").read_text())
    worded = json.loads(json.dumps(data).replace("checking before stop", "checking before you stop"))
    assert hookio.stamp(worded) == hookio.stamp(data)
    del data["hooks"]["PreToolUse"][1]
    assert hookio.stamp(data) != hookio.stamp(worded)


def test_a_missing_or_different_stamp_is_stale():
    assert not hookio.stale([hookio.stamp()])
    assert hookio.stale([]) and hookio.stale(["0" * 12])  # registrations from before the stamp carry none


def test_launch_passes_the_stamp_to_the_hook(tmp_path):
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    shutil.copy(HOOKS / "launch.sh", hooks / "launch.sh")
    (hooks / "stop_gate.py").write_text("import sys\nprint(sys.argv[1:])\n")
    p = subprocess.run(["sh", str(hooks / "launch.sh"), "stop_gate.py", "abc123"], input="{}",
                       capture_output=True, text=True)
    assert p.stdout.strip() == "['abc123']"


def test_the_stamp_is_short_hex():
    assert re.fullmatch(r"[0-9a-f]{12}", hookio.stamp())
