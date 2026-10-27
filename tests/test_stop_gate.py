import io
import json
import sys

import stop_gate
from conftest import DECL, write_decl
from state import State


def inp(root, **kw):
    return {"cwd": str(root), "session_id": "s1", "stop_hook_active": False, **kw}


def passing(calls):
    def check(decl):
        calls.append(1)
        return True, "ok"
    return check


def failing(decl):
    return False, "E   assert 1 == 2"


def test_no_declaration_allows(tmp_path):
    assert stop_gate.evaluate(inp(tmp_path)).action == "allow"


def test_unchanged_tree_runs_the_check_once(repo):
    calls = []
    (repo / "a.py").write_text("x")
    assert stop_gate.evaluate(inp(repo), check=passing(calls)).action == "allow"
    assert stop_gate.evaluate(inp(repo), check=passing(calls)).action == "allow"
    assert len(calls) == 1


def test_failing_check_blocks_with_output(repo):
    (repo / "a.py").write_text("x")
    out = stop_gate.evaluate(inp(repo), check=failing)
    assert out.action == "block"
    assert "assert 1 == 2" in out.reason
    assert "$ true" in out.reason


def test_gives_up_loudly_after_three_blocks(repo):
    (repo / "a.py").write_text("x")
    assert stop_gate.evaluate(inp(repo), check=failing).action == "block"
    for _ in range(2):
        assert stop_gate.evaluate(inp(repo, stop_hook_active=True), check=failing).action == "block"
    out = stop_gate.evaluate(inp(repo, stop_hook_active=True), check=failing)
    assert out.action == "allow"
    assert "CHECK FAILING" in out.warning
    assert "CHECK FAILING" in (repo / "PARKED.md").read_text()


def test_a_new_turn_resets_the_attempt_count(repo):
    (repo / "a.py").write_text("x")
    for active in (False, True, True):
        stop_gate.evaluate(inp(repo, stop_hook_active=active), check=failing)
    assert stop_gate.evaluate(inp(repo), check=failing).action == "block"


def test_real_check_timeout_is_a_named_failure(repo):
    write_decl(repo, 
        DECL.replace('command = "true"', 'command = "sleep 5"').replace("= 30", "= 1"))
    out = stop_gate.evaluate(inp(repo))
    assert out.action == "block"
    assert "timed out after 1s" in out.reason


def test_real_check_output_is_tailed_to_forty_lines(repo):
    write_decl(repo, 
        DECL.replace('command = "true"', 'command = "seq 1 100; exit 1"'))
    out = stop_gate.evaluate(inp(repo))
    assert "\n61\n" in out.reason and "\n60\n" not in out.reason


def test_check_that_rewrites_files_is_not_rerun(repo):
    calls = []

    def formatter(decl):
        calls.append(1)
        (decl.root / "a.py").write_text("formatted")
        return True, ""

    (repo / "a.py").write_text("x")
    assert stop_gate.evaluate(inp(repo), check=formatter).action == "allow"
    assert stop_gate.evaluate(inp(repo), check=formatter).action == "allow"
    assert len(calls) == 1


def test_gate_logs_each_decision(repo):
    (repo / "a.py").write_text("x")
    stop_gate.evaluate(inp(repo), check=failing)
    events = [json.loads(x) for x in (State(repo).dir / "gate.log").read_text().splitlines()]
    assert {"step": "check", "ok": False}.items() <= events[0].items()
    assert events[-1]["action"] == "block"


def run_main(monkeypatch, payload):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    code = stop_gate.main()
    return code, json.loads(buf.getvalue()) if buf.getvalue() else {}


def test_main_blocks_with_the_documented_shape(repo, monkeypatch):
    write_decl(repo, DECL.replace('command = "true"', 'command = "false"'))
    code, out = run_main(monkeypatch, inp(repo))
    assert code == 0 and out["decision"] == "block" and out["reason"]


def test_main_fails_open_and_loud_on_a_broken_declaration(repo, monkeypatch):
    write_decl(repo, "not = [toml")
    code, out = run_main(monkeypatch, inp(repo))
    assert code == 0 and "decision" not in out
    assert "stop gate" in out["systemMessage"]


def test_main_is_inert_inside_the_judge(repo, monkeypatch):
    monkeypatch.setenv("FOREMAN_JUDGE", "1")
    write_decl(repo, DECL.replace('command = "true"', 'command = "false"'))
    assert run_main(monkeypatch, inp(repo)) == (0, {})


import os
import time
from types import SimpleNamespace

from conftest import sh


def ok(decl):
    return True, ""


def legit(*args, **kwargs):  # stands in for the judge, which arrives in Task 5
    return SimpleNamespace(legitimate=True, why="")


def test_frontend_change_needs_a_newer_screenshot(repo):
    fe = repo / "frontend" / "src"
    fe.mkdir(parents=True)
    (fe / "App.tsx").write_text("x")
    out = stop_gate.evaluate(inp(repo), check=ok)
    assert out.action == "block"
    assert ".foreman/walkthrough/" in out.reason and "frontend/src/App.tsx" in out.reason

    shots = State(repo).walkthrough
    shots.mkdir(parents=True)
    old = shots / "old.png"
    old.write_bytes(b"png")
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    assert stop_gate.evaluate(inp(repo, stop_hook_active=True), check=ok).action == "block"

    (shots / "new.png").write_bytes(b"png")
    os.utime(shots / "new.png", (time.time() + 5, time.time() + 5))
    assert stop_gate.evaluate(inp(repo, stop_hook_active=True), check=ok).action == "allow"


def test_backend_change_needs_no_screenshot(repo):
    (repo / "api.py").write_text("x")
    assert stop_gate.evaluate(inp(repo), check=ok).action == "allow"


def test_run_with_commits_needs_the_plan_file_to_move(repo):
    (repo / "PLAN.md").write_text("resume: W8\n")
    sh(repo, "git", "add", "-A")
    sh(repo, "git", "commit", "-q", "-m", "plan")
    assert stop_gate.evaluate(inp(repo), check=ok).action == "allow"
    State(repo).start_run([("PLAN.md", ["W8"])], sh(repo, "git", "rev-parse", "HEAD").strip())

    (repo / "api.py").write_text("x")
    sh(repo, "git", "add", "-A")
    sh(repo, "git", "commit", "-q", "-m", "W8")
    out = stop_gate.evaluate(inp(repo), check=ok)
    assert out.action == "block" and "PLAN.md" in out.reason

    (repo / "PLAN.md").write_text("resume: W9\n")
    assert stop_gate.evaluate(inp(repo, stop_hook_active=True), check=ok, ask=legit).action == "allow"


def test_an_ignored_plan_file_still_counts_when_it_moves(repo):
    (repo / ".gitignore").write_text(".foreman/\nplans/\n")
    (repo / "plans").mkdir()
    (repo / "plans" / "PLAN.md").write_text("resume: W8\n")
    sh(repo, "git", "add", "-A")
    sh(repo, "git", "commit", "-q", "-m", "ignore plans")
    State(repo).start_run([("plans/PLAN.md", ["W8", "W9"])], sh(repo, "git", "rev-parse", "HEAD").strip())

    for unit, after in (("W8", "W9"), ("W9", "done")):
        (repo / "api.py").write_text(unit)
        sh(repo, "git", "add", "-A")
        sh(repo, "git", "commit", "-q", "-m", unit)
        out = stop_gate.evaluate(inp(repo), check=ok, ask=legit)
        assert out.action == "block" and "plans/PLAN.md" in out.reason
        (repo / "plans" / "PLAN.md").write_text(f"resume: {after}\n")
        assert stop_gate.evaluate(inp(repo, stop_hook_active=True), check=ok, ask=legit).action == "allow"


def test_in_a_chain_the_current_plan_moving_is_enough(repo):
    for name in ("A.md", "B.md"):
        (repo / name).write_text("resume: 1\n")
    sh(repo, "git", "add", "-A")
    sh(repo, "git", "commit", "-q", "-m", "plans")
    State(repo).start_run([("A.md", ["a1"]), ("B.md", ["b1"])], sh(repo, "git", "rev-parse", "HEAD").strip())
    (repo / "A.md").write_text("resume: done\n")
    (repo / "api.py").write_text("a1")
    sh(repo, "git", "add", "-A")
    sh(repo, "git", "commit", "-q", "-m", "a1")
    assert stop_gate.evaluate(inp(repo), check=ok, ask=legit).action == "allow"

    (repo / "api.py").write_text("b1")
    sh(repo, "git", "add", "-A")
    sh(repo, "git", "commit", "-q", "-m", "b1")
    out = stop_gate.evaluate(inp(repo), check=ok, ask=legit)
    assert out.action == "block" and "B.md" in out.reason
    (repo / "B.md").write_text("resume: done\n")
    assert stop_gate.evaluate(inp(repo, stop_hook_active=True), check=ok, ask=legit).action == "allow"


def test_commits_outside_a_run_need_no_resume_update(repo):
    (repo / "api.py").write_text("x")
    sh(repo, "git", "add", "-A")
    sh(repo, "git", "commit", "-q", "-m", "x")
    assert stop_gate.evaluate(inp(repo), check=ok).action == "allow"


import judge


def verdict(legit, seen=None):
    def ask(message, run, last_prompt="", running=()):
        if seen is not None:
            seen.update(message=message, last_prompt=last_prompt, running=list(running))
        return judge.Verdict(legit, "Pick the next unit yourself.")
    return ask


def start_run(repo):
    (repo / "PLAN.md").write_text("resume: W8\n")
    State(repo).start_run([("PLAN.md", ["W8", "W9"])], "")


def test_no_run_means_no_judge(repo):
    def boom(*a, **k):
        raise AssertionError("judge called outside a run")
    assert stop_gate.evaluate(inp(repo), check=ok, ask=boom).action == "allow"


def test_judge_blocks_a_sequencing_stop(repo):
    start_run(repo)
    out = stop_gate.evaluate(inp(repo, last_assistant_message="W9 or the UI next?"),
                             check=ok, ask=verdict(False))
    assert out.action == "block"
    assert "Pick the next unit yourself." in out.reason and "Run complete:" in out.reason


def test_judge_allows_and_run_complete_ends_the_run(repo):
    start_run(repo)
    out = stop_gate.evaluate(inp(repo, last_assistant_message="Decision needed: A or B?"),
                             check=ok, ask=verdict(True))
    assert out.action == "allow" and State(repo).run() is not None
    out = stop_gate.evaluate(inp(repo, last_assistant_message="  Run complete: W8, W9 done."),
                             check=ok, ask=verdict(True))
    assert out.action == "allow" and State(repo).run() is None


def test_judge_unavailable_allows_with_a_warning(repo):
    start_run(repo)

    def down(*a, **k):
        raise judge.JudgeUnavailable("timeout")

    out = stop_gate.evaluate(inp(repo), check=ok, ask=down)
    assert out.action == "allow" and "judge unavailable" in out.warning


def test_judge_gives_up_after_three_blocks(repo):
    start_run(repo)
    stop_gate.evaluate(inp(repo), check=ok, ask=verdict(False))
    for _ in range(2):
        stop_gate.evaluate(inp(repo, stop_hook_active=True), check=ok, ask=verdict(False))
    out = stop_gate.evaluate(inp(repo, stop_hook_active=True), check=ok, ask=verdict(False))
    assert out.action == "allow" and "GATE NOT SATISFIED (judge)" in out.warning


def test_a_second_session_is_not_judged(repo):
    start_run(repo)
    assert stop_gate.evaluate(inp(repo), check=ok, ask=verdict(False)).action == "block"

    def boom(*a, **k):
        raise AssertionError("other session judged")

    assert stop_gate.evaluate(inp(repo, session_id="s2"), check=ok, ask=boom).action == "allow"


def test_your_last_prompt_reaches_the_judge(repo, tmp_path):
    start_run(repo)
    t = tmp_path / "t.jsonl"
    rows = [
        {"type": "user", "promptSource": "typed", "message": {"content": "start W8"}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "x"}]}},
        {"type": "user", "promptSource": "typed",
         "message": {"content": [{"type": "text", "text": "what is the sixth unfixed thing?"}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "It is F6."}]}},
    ]
    t.write_text("\n".join(json.dumps(r) for r in rows) + "\n{broken\n")
    seen = {}
    stop_gate.evaluate(inp(repo, transcript_path=str(t)), check=ok, ask=verdict(True, seen))
    assert seen["last_prompt"] == "what is the sixth unfixed thing?"
    assert stop_gate.last_user_prompt("/no/such/file") == ""


# Claude Code runs every subagent in the background and wakes the session when it reports back
# (seen in all pilot runs, Claude Code 2.1.302). Stopping to wait for one is legitimate.

def launched(agent_id, ts="2026-10-26T11:12:47.000Z", tool="Agent"):
    """The Agent call and its result, as the transcript records them: two records joined by the tool_use id."""
    text = (f"Async agent launched successfully.\nagentId: {agent_id} (internal ID - do not mention to user.)\n"
            "The agent is working in the background. You will be notified automatically when it completes.")
    use_id = f"toolu_{agent_id}"
    return [
        {"type": "assistant", "timestamp": ts, "message": {"content": [
            {"type": "tool_use", "id": use_id, "name": tool, "input": {"description": "Task 1"}}]}},
        {"type": "user", "timestamp": ts, "message": {"content": [
            {"type": "tool_result", "tool_use_id": use_id, "content": [{"type": "text", "text": text}]}]}},
    ]


def notified(agent_id, ts="2026-10-26T11:17:23.000Z"):
    return {"type": "queue-operation", "operation": "enqueue", "timestamp": ts, "content": (
        f"<task-notification>\n<task-id>{agent_id}</task-id>\n<status>completed</status>\n"
        "<summary>Agent \"Task 1\" finished</summary>\n</task-notification>")}


def transcript(tmp_path, *rows):
    t = tmp_path / "t.jsonl"
    flat = [r for row in rows for r in (row if isinstance(row, list) else [row])]
    t.write_text("\n".join(json.dumps(r) for r in flat) + "\n")
    return str(t)


NOW = 1793014800.0  # 2026-10-26 11:40 UTC


def boom(*a, **k):
    raise AssertionError("must not run while a subagent is working")


def test_a_stop_while_a_subagent_works_is_allowed_without_check_or_judge(repo, tmp_path):
    start_run(repo)
    (repo / "a.py").write_text("half-written")
    t = transcript(tmp_path, launched("a8278cc01bf5e8bd8"))
    out = stop_gate.evaluate(inp(repo, transcript_path=t), check=boom, ask=boom, now=NOW)
    assert out.action == "allow" and not out.warning
    assert "wait" in (repo / ".foreman" / "gate.log").read_text()


def test_once_the_subagent_reported_back_the_stop_is_gated_again(repo, tmp_path):
    start_run(repo)
    t = transcript(tmp_path, launched("a8278cc01bf5e8bd8"), notified("a8278cc01bf5e8bd8"))
    out = stop_gate.evaluate(inp(repo, transcript_path=t), check=ok, ask=verdict(False), now=NOW)
    assert out.action == "block"


def test_one_finished_subagent_does_not_hide_one_still_working(repo, tmp_path):
    t = transcript(tmp_path, launched("aaa111"), notified("aaa111"), launched("bbb222", "2026-10-26T11:30:00.000Z"))
    assert stop_gate.pending_subagents(t, NOW) == ["bbb222"]


def test_a_subagent_silent_for_hours_is_presumed_lost(repo, tmp_path):
    start_run(repo)
    t = transcript(tmp_path, launched("a8278cc01bf5e8bd8", "2026-10-26T07:00:00.000Z"))
    out = stop_gate.evaluate(inp(repo, transcript_path=t), check=ok, ask=verdict(False), now=NOW)
    assert out.action == "block"


def test_text_that_only_looks_like_a_launch_is_not_a_subagent(tmp_path):
    """A command that prints the launch line must not make the gates wait (security review, 2026-10-27)."""
    spoof = launched("fake0001", tool="Bash")
    t = transcript(tmp_path, spoof)
    assert stop_gate.pending_subagents(t, NOW) == []


def test_an_older_task_tool_launch_counts(tmp_path):
    t = transcript(tmp_path, launched("ddd444", tool="Task"))
    assert stop_gate.pending_subagents(t, NOW) == ["ddd444"]


def test_no_transcript_means_no_subagents():
    assert stop_gate.pending_subagents("", NOW) == []
    assert stop_gate.pending_subagents("/no/such/file", NOW) == []


def test_launch_times_are_read_as_utc(tmp_path):
    t = transcript(tmp_path, launched("ccc333", "2026-10-26T08:41:00.000Z"))  # 2 h 59 min before NOW
    assert stop_gate.pending_subagents(t, NOW) == ["ccc333"]
    assert stop_gate.pending_subagents(t, NOW + 120) == []


# Since Claude Code 2.1.310 the Stop input lists background work itself (sandbox spike, 2026-10-26).

def test_background_tasks_in_the_stop_input_decide(repo, tmp_path):
    start_run(repo)
    (repo / "a.py").write_text("half-written")
    running = [{"id": "a867", "type": "subagent", "status": "running", "agent_type": "general-purpose"}]
    out = stop_gate.evaluate(inp(repo, background_tasks=running), check=boom, ask=boom, now=NOW)
    assert out.action == "allow"


def test_a_background_shell_is_not_a_subagent(repo):
    """A server never exits, so nothing would wake a session that stopped to wait for it."""
    start_run(repo)
    shell = [{"id": "b1", "type": "shell", "status": "running", "command": "npm run dev"}]
    out = stop_gate.evaluate(inp(repo, background_tasks=shell), check=ok, ask=verdict(False), now=NOW)
    assert out.action == "block"


def test_the_judge_is_told_which_background_commands_still_run(repo):
    """Whether a stop that waits for one is legitimate is the judge's call: a test suite ends, a server does not."""
    start_run(repo)
    tasks = [{"id": "b1", "type": "shell", "status": "running", "command": "uv run pytest -m integration"},
             {"id": "b2", "type": "shell", "status": "completed", "command": "make build"}]
    seen = {}
    stop_gate.evaluate(inp(repo, background_tasks=tasks), check=ok, ask=verdict(True, seen), now=NOW)
    assert seen["running"] == ["uv run pytest -m integration"]


def test_an_empty_list_overrides_the_transcript(repo, tmp_path):
    """When Claude Code says nothing runs, a launch line in the transcript without its notification does not count."""
    start_run(repo)
    t = transcript(tmp_path, launched("a8278cc01bf5e8bd8"))
    out = stop_gate.evaluate(inp(repo, transcript_path=t, background_tasks=[]), check=ok, ask=verdict(False), now=NOW)
    assert out.action == "block"


# A declaration changed during a run (by git checkout, reset or pull) must not switch the gates off quietly.

def changed(repo):
    (repo / ".foreman.toml").write_text(DECL + "\n# changed on another branch\n")


def test_a_declaration_changed_during_a_run_blocks_the_stop(repo):
    start_run(repo)
    changed(repo)
    out = stop_gate.evaluate(inp(repo, last_assistant_message="Task 3 is done."), check=boom, ask=boom, now=NOW)
    assert out.action == "block"
    assert "changed since you approved it" in out.reason and "git checkout" in out.reason


def test_restoring_the_approved_declaration_reopens_the_gates(repo):
    start_run(repo)
    original = (repo / ".foreman.toml").read_text()
    changed(repo)
    stop_gate.evaluate(inp(repo), check=boom, ask=boom, now=NOW)
    (repo / ".foreman.toml").write_text(original)
    out = stop_gate.evaluate(inp(repo, stop_hook_active=True), check=ok, ask=verdict(False), now=NOW)
    assert out.action == "block" and "Pick the next unit yourself." in out.reason  # the judge is back


def test_handing_back_to_the_user_is_let_through(repo):
    start_run(repo)
    changed(repo)
    out = stop_gate.evaluate(inp(repo, last_assistant_message="Blocked on you: .foreman.toml changed, please review."),
                             check=boom, ask=boom, now=NOW)
    assert out.action == "allow" and "not approved" in out.warning


def test_a_changed_declaration_gives_up_after_three_blocks(repo):
    start_run(repo)
    changed(repo)
    assert stop_gate.evaluate(inp(repo), check=boom, ask=boom, now=NOW).action == "block"
    for _ in range(2):
        assert stop_gate.evaluate(inp(repo, stop_hook_active=True), check=boom, ask=boom, now=NOW).action == "block"
    out = stop_gate.evaluate(inp(repo, stop_hook_active=True), check=boom, ask=boom, now=NOW)
    assert out.action == "allow" and "DECLARATION CHANGED" in out.warning


def test_outside_a_run_a_changed_declaration_only_warns(repo):
    changed(repo)
    out = stop_gate.evaluate(inp(repo), check=boom, ask=boom, now=NOW)
    assert out.action == "allow" and "not approved" in out.warning


def test_waiting_for_a_subagent_is_still_allowed_with_a_changed_declaration(repo):
    """Blocking the wait would send the session back to sleep loops; the next stop after the report holds it."""
    start_run(repo)
    changed(repo)
    running = [{"id": "a867", "type": "subagent", "status": "running"}]
    assert stop_gate.evaluate(inp(repo, background_tasks=running), check=boom, ask=boom, now=NOW).action == "allow"
