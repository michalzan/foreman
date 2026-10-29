"""report.py: does a run's gate log show Foreman working as intended? It reads structure only, never content."""
import json

import report

T = "2026-10-28T21:{:02d}:00"


def rows(*events):
    return [{"ts": T.format(i), **e} for i, e in enumerate(events)]


START = {"step": "run", "action": "start", "units": 2, "head": "abc"}
LAUNCH = lambda a: {"step": "wait", "subagents": [a]}
CHECKED = {"step": "unit_check", "ok": True, "seconds": 1.0}
SKIPPED = {"step": "unit_check", "action": "skip", "why": "passed"}
DONE = {"step": "stop", "action": "allow", "opening": "complete"}
END = {"step": "run", "action": "end", "how": "complete"}


def problems(events):
    [run] = report.runs(events)
    return [line for status, line in report.invariants(run) if status == "FAIL"]


def test_a_clean_run_has_no_problems():
    assert problems(rows(START, SKIPPED, LAUNCH("a1"), LAUNCH("a1"), CHECKED, LAUNCH("a2"), END, DONE)) == []


def test_launches_without_the_unit_gate_are_named():
    """The A6 pilot run (2026-10-28): five launches, no unit_check at all."""
    out = problems(rows(START, LAUNCH("a1"), LAUNCH("a2"), LAUNCH("a3"), END, DONE))
    assert out == ["unit gate: no decision before 3 of 3 subagent launches (hooks loaded before an update?)"]


def test_a_launch_seen_twice_is_one_launch():
    assert problems(rows(START, CHECKED, LAUNCH("a1"), LAUNCH("a1"), LAUNCH("a1"), END, DONE)) == []


def test_gave_ups_holds_and_stale_hooks_are_named():
    out = problems(rows(START, {"step": "check", "action": "gave_up"}, {"step": "declaration", "action": "block"},
                        {"step": "hooks", "action": "block"}, {"step": "judge", "action": "unavailable"}, END, DONE))
    assert "gave up: check (1)" in out
    assert "declaration changed during the run: held 1 time(s)" in out
    assert any(line.startswith("hooks loaded before an update") for line in out)
    assert "judge unavailable at 1 stop(s)" in out


def test_a_failing_check_left_failing_is_named():
    fail = {"step": "check", "ok": False, "seconds": 2.0}
    assert "check: failing at the end of the run" in problems(rows(START, fail, END, DONE))
    assert problems(rows(START, fail, {"step": "check", "ok": True, "seconds": 2.0}, END, DONE)) == []


def test_a_run_that_did_not_hand_back_is_named():
    assert "no hand-back: the log ends without Run complete, Blocked on you or Decision needed" in problems(
        rows(START, {"step": "stop", "action": "allow", "opening": "other"}))
    assert problems(rows(START, {"step": "stop", "action": "allow", "opening": "blocked"})) == []


def test_talk_after_a_hand_back_keeps_it():
    """A pilot run (2026-10-29): Blocked on you, then the user ran /foreman:report in the same session."""
    run = rows(START, CHECKED, LAUNCH("a1"), {"step": "check", "ok": True, "seconds": 1.0},
               {"step": "stop", "action": "allow", "opening": "blocked"},
               {"step": "stop", "action": "allow", "opening": "other"})
    assert problems(run) == []
    assert ("ok", "hand-back: blocked") in report.invariants(report.runs(run)[0])


def test_work_after_the_last_hand_back_needs_another():
    assert "no hand-back: the log ends without Run complete, Blocked on you or Decision needed" in problems(
        rows(START, {"step": "stop", "action": "allow", "opening": "blocked"}, CHECKED, LAUNCH("a1"),
             {"step": "stop", "action": "allow", "opening": "other"}))


def test_runs_are_split_at_their_markers_and_an_unmarked_log_is_one_run():
    events = rows(START, END, DONE, START, END, DONE)
    assert len(report.runs(events)) == 2
    assert len(report.runs(rows(LAUNCH("a1"), CHECKED))) == 1


def test_the_report_carries_no_content(tmp_path):
    log = tmp_path / ".foreman" / "gate.log"
    log.parent.mkdir()
    log.write_text("\n".join(json.dumps(e) for e in rows(START, LAUNCH("a1"), END, DONE)) + "\n")
    text = report.render(tmp_path, versions={"foreman": "0.3.0", "claude": "2.1.310", "os": "macOS"})
    assert "a1" not in text and "abc" not in text
    assert "foreman 0.3.0" in text and "Run 1" in text and "unit gate" in text


def test_an_old_log_does_not_claim_what_it_cannot_show():
    [run] = report.runs(rows(LAUNCH("a1"), CHECKED))
    assert [status for status, line in report.invariants(run) if line.startswith(("hooks", "unit gate", "hand-back"))] \
        == ["?", "?", "?"]
