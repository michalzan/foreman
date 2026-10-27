"""Tests for the findings of the whole-branch review. Each failed before its fix."""
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import declaration
import doctor
import guard
import judge
import stop_gate
from conftest import DECL, sh, write_decl
from state import State


def inp(root, **kw):
    return {"cwd": str(root), "session_id": "s1", "stop_hook_active": False, **kw}


def ok(decl):
    return True, ""


def bad(decl):
    return False, "boom"


def must_not_run(decl):
    raise AssertionError("check ran")


def nudge(message, run, last_prompt="", running=()):
    return judge.Verdict(False, "Carry on.")


def legit(*args, **kwargs):
    return judge.Verdict(True, "")


def start(repo, units=("U1", "U2", "U3", "U4", "U5")):
    (repo / "PLAN.md").write_text("resume: U1\n")
    sh(repo, "git", "add", "-A")
    sh(repo, "git", "commit", "-q", "-m", "plan")
    State(repo).start_run([("PLAN.md", list(units))], sh(repo, "git", "rev-parse", "HEAD").strip())


def commit_unit(repo, name):
    (repo / name).write_text("x")
    (repo / "PLAN.md").write_text(f"resume after {name}\n")
    sh(repo, "git", "add", "-A")
    sh(repo, "git", "commit", "-q", "-m", name)


# 1. the judge's nudges must not run out while the run makes progress
def test_judge_keeps_nudging_while_the_run_makes_progress(repo):
    start(repo)
    for i in range(6):
        commit_unit(repo, f"u{i}.py")
        out = stop_gate.evaluate(inp(repo, stop_hook_active=True), check=ok, ask=nudge)
        assert out.action == "block", f"stop {i} was allowed: {out.warning}"


def test_judge_gives_up_after_three_nudges_without_progress(repo):
    start(repo)
    actions = [stop_gate.evaluate(inp(repo, stop_hook_active=True), check=ok, ask=nudge).action
               for _ in range(4)]
    assert actions == ["block", "block", "block", "allow"]


def test_nudges_do_not_spend_the_check_attempts(repo):
    start(repo)
    for _ in range(3):
        stop_gate.evaluate(inp(repo, stop_hook_active=True), check=ok, ask=nudge)
    (repo / "a.py").write_text("x")
    assert stop_gate.evaluate(inp(repo, stop_hook_active=True), check=bad, ask=nudge).action == "block"


# 2. a declaration below the git top level must not record a broken tree as passed
def test_declaration_below_the_git_top_level_is_refused_loudly(tmp_path):
    top = tmp_path / "mono"
    proj = top / "proj"
    proj.mkdir(parents=True)
    sh(top, "git", "init", "-q", "-b", "dev")
    write_decl(proj, DECL)
    (proj / "DECISIONS.md").write_text("")
    (proj / "PARKED.md").write_text("")
    out = stop_gate.evaluate(inp(proj), check=must_not_run)
    assert out.action == "allow" and "top level" in out.warning
    found = doctor.findings(proj, tmp_path / "home")
    assert len(found) == 1 and "top level" in found[0]


# 3 + 4. guard
ASK = [
    "echo '{}' > ~/.claude/foreman-trusted.json",
    "rm ~/.claude/foreman-trusted.json",
    "python3 tooling/claude/harness/hooks/trust.py approve",
    'H="$HOME/.claude/hooks/harness"; uv run "$H/trust.py" approve',
    "python3 hooks/run.py stop",
    "rm .foreman/run.json",
    "FOO=1 git push origin main",
    "GIT_SSH_COMMAND=x git push origin main",
    "command git push origin main",
    "time git push origin main",
    "/usr/bin/git push origin main",
    "(git push origin main)",
    'bash -c "git push origin main"',
    "if true; then git push origin main; fi",
    "git push origin $B",
    "git push origin $(cat branch.txt)",
    "git push -o ci.skip origin main",
]
ASK_ON_MAIN = ["git push origin 2>&1", "git push > /dev/null", "git push -o x origin", "git push origin @"]
PASS = ["git stash push", "git stash push -m wip", "git push origin dev 2>&1", "FOO=1 git push origin dev",
        "git push -o ci.skip origin dev", 'bash -c "git push origin dev"']


@pytest.mark.parametrize("command", ASK)
def test_guard_asks(repo, tmp_path, command):
    assert guard.decide({"cwd": str(repo), "tool_name": "Bash", "tool_input": {"command": command}}, home=tmp_path)


@pytest.mark.parametrize("command", ASK_ON_MAIN)
def test_guard_asks_for_bare_pushes_on_a_protected_branch(repo, tmp_path, command):
    payload = {"cwd": str(repo), "tool_name": "Bash", "tool_input": {"command": command}}
    assert guard.decide(payload, home=tmp_path) is None  # on dev
    sh(repo, "git", "checkout", "-q", "-b", "main")
    assert guard.decide(payload, home=tmp_path)


@pytest.mark.parametrize("command", PASS)
def test_guard_passes(repo, tmp_path, command):
    sh(repo, "git", "checkout", "-q", "-b", "main") if "stash" in command else None
    assert guard.decide({"cwd": str(repo), "tool_name": "Bash", "tool_input": {"command": command}}, home=tmp_path) is None


# 5. an unapproved declaration must say so at the stop, not only at the next session start
def test_unapproved_declaration_is_announced_at_every_stop(repo):
    (repo / ".foreman.toml").write_text(DECL + "\n# changed\n")
    out = stop_gate.evaluate(inp(repo), check=must_not_run)
    assert out.action == "allow" and "not approved" in out.warning


# 6. the installer must not damage settings.json: install.py is gone (the harness is a plugin since
#    2026-10-26); the same rule for uninstall.py is in test_uninstall.py


# 7. the guard must fail closed even when it cannot import its own modules
def test_guard_fails_closed_when_a_sibling_module_is_missing(tmp_path):
    hooks = tmp_path / "hooks"
    shutil.copytree(Path(guard.__file__).parent, hooks, ignore=shutil.ignore_patterns("__pycache__"))
    (hooks / "trust.py").unlink()
    p = subprocess.run([sys.executable, str(hooks / "guard.py")], text=True, capture_output=True,
                       input='{"tool_name": "Bash", "tool_input": {"command": "ls"}}')
    assert p.returncode == 2


# re-graded minors
def test_first_stop_of_a_run_sees_frontend_files_committed_since_it_started(repo):
    start(repo, units=("U1",))
    (repo / "frontend" / "src").mkdir(parents=True)
    (repo / "frontend" / "src" / "App.tsx").write_text("x")
    commit_unit(repo, "api.py")
    out = stop_gate.evaluate(inp(repo), check=ok, ask=legit)
    assert out.action == "block" and "walkthrough" in out.reason


@pytest.mark.parametrize("old, new", [
    ('protected = ["test", "main"]', 'protected = "main"'),
    ('paths = ["frontend/src/"]', 'paths = "frontend/src/"'),
    ('files = ["Makefile"]', 'files = "Makefile"'),
])
def test_list_fields_must_be_lists(repo, old, new):
    write_decl(repo, DECL.replace(old, new))
    with pytest.raises(declaration.DeclarationError):
        declaration.load(repo)


# Fail closed: find `git ... push` wherever it sits in a command, not only after known wrappers.
MORE_ASK = [
    "env FOO=1 git push origin main",
    "timeout 30 git push origin main",
    "nice -n 5 git push origin main",
    "xargs git push origin main",
    'bash -lc "git push origin main"',
    'eval "git push origin main"',
    "echo main | xargs -I{} sh -c 'git push origin {} main'",
    "git -c alias.p=push p origin main",
]
MORE_PASS = ["env FOO=1 git push origin dev", 'bash -lc "git push origin dev"', "timeout 30 git fetch origin main",
             'git commit -m "push to main tomorrow"']


@pytest.mark.parametrize("command", MORE_ASK)
def test_guard_finds_a_push_behind_any_wrapper(repo, tmp_path, command):
    assert guard.decide({"cwd": str(repo), "tool_name": "Bash", "tool_input": {"command": command}}, home=tmp_path)


@pytest.mark.parametrize("command", MORE_PASS)
def test_guard_still_passes_ordinary_commands(repo, tmp_path, command):
    assert guard.decide({"cwd": str(repo), "tool_name": "Bash", "tool_input": {"command": command}}, home=tmp_path) is None
