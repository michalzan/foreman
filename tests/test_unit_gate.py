"""The check between units: run before the next subagent starts, while nothing else writes to the tree.

The stop gate cannot do it: during a run every stop between units is a wait for a subagent, and the
tree is half-written then. In the A7 pilot run (2026-10-27) the check ran once in nine units, at the end.
"""
import io
import json
import sys

import unit_gate
from conftest import DECL, write_decl
from state import State

from test_stop_gate import NOW, launched, transcript


def inp(root, **kw):
    return {"cwd": str(root), "session_id": "s1", "tool_name": "Agent", **kw}


def start_run(repo):
    (repo / "PLAN.md").write_text("resume: W8\n")
    State(repo).start_run([("PLAN.md", ["W8", "W9"])], "")


def passing(calls):
    def check(decl):
        calls.append(1)
        return True, "ok"
    return check


def failing(decl):
    return False, "E   assert 1 == 2"


def boom(decl):
    raise AssertionError("the check must not run here")


def test_outside_a_run_nothing_is_checked(repo):
    (repo / "a.py").write_text("x")
    assert unit_gate.evaluate(inp(repo), check=boom, now=NOW).action == "allow"


def test_a_changed_tree_is_checked_before_the_next_subagent(repo):
    start_run(repo)
    (repo / "a.py").write_text("x")
    calls = []
    assert unit_gate.evaluate(inp(repo), check=passing(calls), now=NOW).action == "allow"
    assert calls == [1]
    assert '"step": "unit_check"' in (repo / ".foreman" / "gate.log").read_text()


def test_a_tree_that_already_passed_is_not_checked_again(repo):
    start_run(repo)
    (repo / "a.py").write_text("x")
    calls = []
    unit_gate.evaluate(inp(repo), check=passing(calls), now=NOW)
    unit_gate.evaluate(inp(repo), check=passing(calls), now=NOW)
    assert calls == [1]


def test_a_failing_check_denies_the_launch_with_its_output(repo):
    start_run(repo)
    (repo / "a.py").write_text("x")
    out = unit_gate.evaluate(inp(repo), check=failing, now=NOW)
    assert out.action == "deny"
    assert "assert 1 == 2" in out.reason and "$ true" in out.reason


def test_after_three_denials_the_launch_goes_ahead_and_is_parked(repo):
    start_run(repo)
    (repo / "a.py").write_text("x")
    for _ in range(3):
        assert unit_gate.evaluate(inp(repo), check=failing, now=NOW).action == "deny"
    out = unit_gate.evaluate(inp(repo), check=failing, now=NOW)
    assert out.action == "allow" and "CHECK FAILING" in out.warning
    assert "CHECK FAILING" in (repo / "PARKED.md").read_text()


def test_a_passing_check_resets_the_denials(repo):
    start_run(repo)
    (repo / "a.py").write_text("x")
    for _ in range(2):
        unit_gate.evaluate(inp(repo), check=failing, now=NOW)
    unit_gate.evaluate(inp(repo), check=passing([]), now=NOW)
    (repo / "a.py").write_text("y")
    for _ in range(3):
        assert unit_gate.evaluate(inp(repo), check=failing, now=NOW).action == "deny"


def test_a_subagent_launching_its_own_is_not_checked(repo):
    start_run(repo)
    (repo / "a.py").write_text("half-written")
    assert unit_gate.evaluate(inp(repo, agent_id="a8278"), check=boom, now=NOW).action == "allow"


def test_while_another_subagent_works_the_tree_is_not_checked(repo, tmp_path):
    start_run(repo)
    (repo / "a.py").write_text("half-written")
    t = transcript(tmp_path, launched("a8278cc01bf5e8bd8"))
    assert unit_gate.evaluate(inp(repo, transcript_path=t), check=boom, now=NOW).action == "allow"


def test_an_unapproved_declaration_is_never_run(repo):
    (repo / ".foreman.toml").write_text(DECL.replace('command = "true"', 'command = "false"'))  # changed, not approved
    (repo / "a.py").write_text("x")
    assert unit_gate.evaluate(inp(repo), check=boom, now=NOW).action == "allow"


# A declaration changed during a run (by git checkout, reset or pull) must not switch the gates off quietly.

def test_a_declaration_changed_during_a_run_refuses_the_next_unit(repo):
    start_run(repo)
    (repo / ".foreman.toml").write_text(DECL.replace('command = "true"', 'command = "false"'))
    out = unit_gate.evaluate(inp(repo), check=boom, now=NOW)  # its check is still never run
    assert out.action == "deny" and "changed since you approved it" in out.reason


def test_a_changed_declaration_gives_up_after_three_refusals(repo):
    start_run(repo)
    (repo / ".foreman.toml").write_text(DECL + "\n# changed\n")
    for _ in range(3):
        assert unit_gate.evaluate(inp(repo), check=boom, now=NOW).action == "deny"
    out = unit_gate.evaluate(inp(repo), check=boom, now=NOW)
    assert out.action == "allow" and "DECLARATION CHANGED" in out.warning


def test_main_denies_with_the_documented_shape(repo, monkeypatch):
    start_run(repo)
    (repo / "a.py").write_text("x")
    write_decl(repo, DECL.replace('command = "true"', 'command = "false"'))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(inp(repo))))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    assert unit_gate.main() == 0
    hso = json.loads(out.getvalue())["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse" and hso["permissionDecision"] == "deny"
    assert "foreman:" in hso["permissionDecisionReason"]


def test_main_fails_open_and_loud(repo, monkeypatch):
    start_run(repo)
    write_decl(repo, "not = [toml")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(inp(repo))))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    assert unit_gate.main() == 0
    assert "unit gate crashed" in json.loads(out.getvalue())["systemMessage"]


def test_only_a_subagent_launch_is_gated(repo):
    """The matcher Agent|Task may also catch TaskOutput or TaskStop; those must never wait for the check."""
    start_run(repo)
    (repo / "a.py").write_text("x")
    assert unit_gate.evaluate(inp(repo, tool_name="TaskOutput"), check=boom, now=NOW).action == "allow"


def test_a_printed_launch_line_does_not_skip_the_check(repo, tmp_path):
    start_run(repo)
    (repo / "a.py").write_text("x")
    t = transcript(tmp_path, launched("fake0001", tool="Bash"))
    calls = []
    unit_gate.evaluate(inp(repo, transcript_path=t), check=passing(calls), now=NOW)
    assert calls == [1]


def test_restoring_the_declaration_resets_the_refusals(repo):
    start_run(repo)
    original = (repo / ".foreman.toml").read_text()
    (repo / ".foreman.toml").write_text(DECL + "\n# changed\n")
    for _ in range(2):
        unit_gate.evaluate(inp(repo), check=boom, now=NOW)
    (repo / ".foreman.toml").write_text(original)
    unit_gate.evaluate(inp(repo), check=passing([]), now=NOW)
    (repo / ".foreman.toml").write_text(DECL + "\n# changed again\n")
    for _ in range(3):
        assert unit_gate.evaluate(inp(repo), check=boom, now=NOW).action == "deny"
