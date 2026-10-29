import io
import json
import sys

import audit
import doctor
from conftest import DECL, sh, write_decl
from state import State

def make_home(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text("{}")
    return home


def make_plugin(tmp_path, events=("SessionStart", "PreToolUse", "Stop"), skip=()):
    """A plugin root whose hooks.json registers the given events, without the scripts in skip."""
    scripts = {"SessionStart": ["doctor.py"], "PreToolUse": ["guard.py", "unit_gate.py"], "Stop": ["stop_gate.py"]}
    root = tmp_path / "plugin"
    (root / "hooks").mkdir(parents=True)
    hooks = {e: [{"hooks": [{"type": "command", "command": f'sh "${{CLAUDE_PLUGIN_ROOT}}/hooks/launch.sh" {name}'}]}
                 for name in scripts[e] if name not in skip]
             for e in events}
    (root / "hooks" / "hooks.json").write_text(json.dumps({"hooks": hooks}))
    for names in scripts.values():
        for name in names:
            (root / "hooks" / name).write_text("")
    return root


def audited(repo):
    fp = audit.fingerprint(repo, ["Makefile"])
    write_decl(repo, DECL.replace('fingerprint = ""', f'fingerprint = "{fp}"'))
    return repo


def test_audit_fingerprint_tracks_content_and_missing_files(repo):
    a = audit.fingerprint(repo, ["Makefile"])
    assert a.startswith("sha256:") and a == audit.fingerprint(repo, ["Makefile"])
    (repo / "Makefile").write_text("check:\n\tfalse\n")
    assert audit.fingerprint(repo, ["Makefile"]) != a
    assert audit.fingerprint(repo, ["Makefile", "gone.json"]) != audit.fingerprint(repo, ["Makefile"])


def test_undeclared_project_gets_one_line(tmp_path):
    out = doctor.findings(tmp_path, make_home(tmp_path))
    assert len(out) == 1 and "gates are off" in out[0]


def test_healthy_project_has_no_findings(repo, tmp_path):
    assert doctor.findings(audited(repo), make_home(tmp_path)) == []


def test_each_weakening_is_reported(repo, tmp_path):
    home = make_home(tmp_path)
    audited(repo)

    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.local.json").write_text('{"disableAllHooks": true}')
    assert any("disableAllHooks" in f for f in doctor.findings(repo, home))
    (repo / ".claude" / "settings.local.json").unlink()

    (repo / "Makefile").write_text("check:\n\tfalse\n")
    assert any("audit is stale" in f for f in doctor.findings(repo, home))
    sh(repo, "git", "checkout", "-q", "Makefile")

    (repo / "PARKED.md").unlink()
    assert any("missing PARKED.md" in f for f in doctor.findings(repo, home))
    (repo / "PARKED.md").write_text("# Parked\n")

    (repo / ".gitignore").write_text("")
    assert any("not gitignored" in f for f in doctor.findings(repo, home))
    (repo / ".gitignore").write_text(".foreman/\n")

    assert doctor.findings(repo, home) == []


def test_the_real_plugin_registers_every_hook(repo, tmp_path):
    assert doctor.findings(audited(repo), make_home(tmp_path)) == []


def test_a_hook_missing_from_the_plugin_is_reported(repo, tmp_path):
    plugin = make_plugin(tmp_path, events=("SessionStart", "PreToolUse"))
    out = doctor.findings(audited(repo), make_home(tmp_path), plugin_root=plugin)
    assert out == ["Stop hook missing from the plugin's hooks/hooks.json (stop_gate.py)"]
    (plugin / "hooks" / "guard.py").unlink()
    assert any("guard.py is missing" in f for f in doctor.findings(repo, make_home(tmp_path / "b"), plugin_root=plugin))


def test_a_plugin_without_the_unit_gate_is_reported(repo, tmp_path):
    plugin = make_plugin(tmp_path, skip=("unit_gate.py",))
    out = doctor.findings(audited(repo), make_home(tmp_path), plugin_root=plugin)
    assert out == ["PreToolUse hook missing from the plugin's hooks/hooks.json (unit_gate.py)"]


def test_an_old_install_beside_the_plugin_is_reported(repo, tmp_path):
    home = make_home(tmp_path)
    audited(repo)
    (home / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": "uv run ~/.claude/hooks/harness/stop_gate.py"}]}]}}))
    assert any("old install" in f and "uninstall.py" in f for f in doctor.findings(repo, home))
    (home / ".claude" / "settings.json").write_text("{}")
    (home / ".claude" / "hooks" / "harness").mkdir(parents=True)
    assert any("old install" in f for f in doctor.findings(repo, home))


def test_no_audit_and_broken_declaration(repo, tmp_path):
    home = make_home(tmp_path)
    assert any("no audit recorded" in f for f in doctor.findings(repo, home))
    write_decl(repo, "not = [toml")
    out = doctor.findings(repo, home)
    assert len(out) == 1 and "unreadable" in out[0]


def test_active_run_is_announced(repo, tmp_path):
    State(audited(repo)).start_run([("PLAN.md", ["W8", "W9"])], "")
    home = make_home(tmp_path)
    resume = ("continue from the resume point in PLAN.md; decisions so far are in DECISIONS.md, "
              "parked questions in PARKED.md")
    assert doctor.findings(repo, home) == ["run active: W8, W9 (PLAN.md)", resume]
    State(repo).start_run([("PLAN.md", ["W8"])], "", plain=True)
    assert doctor.findings(repo, home) == ["run active: W8 (PLAN.md), plain: skip the simplify pass", resume]


def test_a_chain_is_announced_in_order(repo, tmp_path):
    State(audited(repo)).start_run([("A.md", ["a1", "a2"]), ("B.md", ["b1"])], "")
    found = doctor.findings(repo, make_home(tmp_path))
    assert found[0] == "run active: a1, a2 (A.md); then b1 (B.md)"
    assert "A.md, then B.md" in found[1]


def test_stale_audit_during_a_run_waits_for_the_hand_back(repo, tmp_path):
    State(audited(repo)).start_run([("PLAN.md", ["W8"])], "")
    (repo / "Makefile").write_text("check:\n\tfalse\n")
    stale = [f for f in doctor.findings(repo, make_home(tmp_path)) if "audit is stale" in f]
    assert stale and "after the run" in stale[0] and "not during it" in stale[0]


def test_session_start_output_shape(repo, tmp_path, monkeypatch):
    home = make_home(tmp_path)
    monkeypatch.setattr(doctor.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"cwd": str(repo)})))
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    assert doctor.main([doctor.hookio.stamp()]) == 0
    out = json.loads(buf.getvalue())
    assert "no audit recorded" in out["systemMessage"]
    assert out["hookSpecificOutput"] == {"hookEventName": "SessionStart",
                                         "additionalContext": out["systemMessage"]}


def test_tunnels_are_named_during_a_run(repo, tmp_path, monkeypatch):
    import tunnels
    monkeypatch.setattr(tunnels, "warning", lambda run=None: "local ports held by tunnels: 8090 (ssh x)")
    home = make_home(tmp_path)
    assert doctor.findings(audited(repo), home) == []  # outside a run they do not stop a run from starting
    State(repo).start_run([("PLAN.md", ["W8"])], "")
    assert "local ports held by tunnels: 8090 (ssh x)" in doctor.findings(repo, home)


def test_during_a_run_the_session_start_context_carries_the_contract(repo, tmp_path, monkeypatch, capsys):
    """After compaction the run contract must come back; outside a run it stays out of context."""
    audited(repo)
    monkeypatch.setattr(doctor.hookio, "read_input", lambda: {"cwd": str(repo)})
    home = make_home(tmp_path)
    monkeypatch.setattr(doctor.Path, "home", lambda: home)
    doctor.main([doctor.hookio.stamp()])
    assert capsys.readouterr().out == ""
    State(repo).start_run([("PLAN.md", ["W8"])], "")
    doctor.main([doctor.hookio.stamp()])
    context = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "run active: W8" in context and "## Run contract" in context


def test_the_user_sees_the_name_foreman(repo, tmp_path, monkeypatch, capsys):
    import guard
    audited(repo)
    monkeypatch.chdir(repo)
    home = make_home(tmp_path)
    monkeypatch.setattr(doctor.Path, "home", lambda: home)
    doctor.main(["--cli"])
    assert capsys.readouterr().out.strip() == "foreman: ok"
    monkeypatch.setattr(guard.hookio, "read_input", lambda: {"cwd": str(repo), "tool_name": "Bash",
                                                              "tool_input": {"command": "git push origin main"}})
    guard.main()
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecisionReason"].startswith("foreman: ")


def test_a_project_with_the_old_names_is_told_to_migrate(tmp_path):
    root = tmp_path / "old"
    (root / "sub").mkdir(parents=True)
    (root / ".harness.toml").write_text(DECL)
    out = doctor.findings(root / "sub", make_home(tmp_path))
    assert len(out) == 1 and "old file names" in out[0] and "migrate.py" in out[0]


def test_hooks_loaded_before_they_last_changed_are_named(repo, tmp_path, monkeypatch, capsys):
    """/clear does not reload hooks.json; the A6 pilot run (2026-10-28) ran without the unit gate because of it."""
    audited(repo)
    monkeypatch.setattr(doctor.hookio, "read_input", lambda: {"cwd": str(repo)})
    home = make_home(tmp_path)
    monkeypatch.setattr(doctor.Path, "home", lambda: home)
    doctor.main(["0" * 12])
    assert "quit claude and start it again" in json.loads(capsys.readouterr().out)["systemMessage"]
    doctor.main([doctor.hookio.stamp()])
    assert capsys.readouterr().out == ""


def test_the_session_start_records_where_the_session_began(repo, tmp_path, monkeypatch, capsys):
    audited(repo)
    monkeypatch.setattr(doctor.hookio, "read_input", lambda: {"cwd": str(repo), "session_id": "s9"})
    monkeypatch.setattr(doctor.Path, "home", lambda: make_home(tmp_path))
    doctor.main([doctor.hookio.stamp()])
    assert State(repo).session_head("s9") == doctor.gitutil.head(repo)


def test_a_failed_record_of_the_session_start_keeps_the_findings(repo, tmp_path, monkeypatch, capsys):
    audited(repo)
    monkeypatch.setattr(doctor.hookio, "read_input", lambda: {"cwd": str(repo), "session_id": "s9"})
    monkeypatch.setattr(doctor.Path, "home", lambda: make_home(tmp_path))
    monkeypatch.setattr(doctor, "findings", lambda cwd, home: ["missing PARKED.md"])
    def broken(self, sid, head):
        raise OSError("state directory is a symlink")
    monkeypatch.setattr(State, "session_started", broken)
    doctor.main([doctor.hookio.stamp()])
    message = json.loads(capsys.readouterr().out)["systemMessage"]
    assert "missing PARKED.md" in message and "crashed" not in message
