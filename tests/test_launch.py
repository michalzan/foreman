"""launch.sh: the guard fails closed even when Python itself cannot run it (security review, 2026-10-27)."""
import shutil
import subprocess
from pathlib import Path

LAUNCH = Path(__file__).resolve().parent.parent / "hooks" / "launch.sh"


def run(tmp_path, script, body):
    hooks = tmp_path / "hooks"
    hooks.mkdir(exist_ok=True)
    shutil.copy(LAUNCH, hooks / "launch.sh")
    (hooks / script).write_text(body)
    return subprocess.run(["sh", str(hooks / "launch.sh"), script], input="{}", capture_output=True, text=True)


def test_a_guard_that_does_not_compile_blocks(tmp_path):
    assert run(tmp_path, "guard.py", "def broken(:\n").returncode == 2


def test_a_guard_that_answers_passes_its_answer_through(tmp_path):
    p = run(tmp_path, "guard.py", 'print("{\\"ok\\": 1}")\n')
    assert p.returncode == 0 and p.stdout.strip() == '{"ok": 1}'


def test_other_hooks_keep_their_exit_codes(tmp_path):
    assert run(tmp_path, "stop_gate.py", "raise SystemExit(1)\n").returncode == 1
