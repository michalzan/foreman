import json
import subprocess
from types import SimpleNamespace

import pytest

import judge

RUN = {"plan_file": "PLAN.md", "units": ["W8", "W9"]}


def fake(stdout, seen=None):
    def runner(cmd, **kw):
        if seen is not None:
            seen.update(cmd=cmd, **kw)
        return SimpleNamespace(stdout=stdout, returncode=0)
    return runner


def test_parses_structured_output_and_builds_a_safe_command():
    seen = {}
    out = json.dumps({"structured_output": {"legitimate": False, "why": "Pick the next unit."}})
    v = judge.ask("Next: W9 or the UI?", RUN, "go", runner=fake(out, seen))
    assert v == judge.Verdict(False, "Pick the next unit.")
    cmd = seen["cmd"]
    assert cmd[:2] == ["claude", "-p"] and "--bare" not in cmd
    assert cmd[cmd.index("--settings") + 1] == '{"disableAllHooks":true}'
    assert cmd[cmd.index("--tools") + 1] == ""
    assert seen["env"]["FOREMAN_JUDGE"] == "1"
    assert "W8, W9" in seen["input"] and "Next: W9 or the UI?" in seen["input"] and "go" in seen["input"]


def test_long_messages_keep_their_opening_and_their_ending():
    seen = {}
    out = json.dumps({"structured_output": {"legitimate": True, "why": "ok"}})
    judge.ask("Run complete: " + "x" * 20000 + " THE END", RUN, runner=fake(out, seen))
    assert "Run complete:" in seen["input"] and "THE END" in seen["input"]
    assert len(seen["input"]) < 9000


@pytest.mark.parametrize("stdout", ["", "not json", "{}", '{"structured_output": {"why": "x"}}'])
def test_unusable_output_is_unavailable(stdout):
    with pytest.raises(judge.JudgeUnavailable):
        judge.ask("m", RUN, runner=fake(stdout))


def test_timeout_and_missing_cli_are_unavailable():
    def timeout(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)

    def missing(cmd, **kw):
        raise FileNotFoundError("claude")

    for runner in (timeout, missing):
        with pytest.raises(judge.JudgeUnavailable):
            judge.ask("m", RUN, runner=runner)


def test_the_judge_sees_every_plan_in_a_chain():
    seen = {}

    def runner(cmd, **kw):
        seen["input"] = kw.get("input", "") + " ".join(cmd)
        return subprocess.CompletedProcess(cmd, 0, json.dumps(
            {"structured_output": {"legitimate": True, "why": "x"}}), "")
    judge.ask("m", {"plans": [{"file": "A.md", "units": ["a1"]}, {"file": "B.md", "units": ["b1"]}],
                    "units": ["a1", "b1"]}, runner=runner)
    assert "A.md: a1" in seen["input"] and "B.md: b1" in seen["input"]


def test_the_judge_sees_background_commands_still_running():
    seen = {}
    out = json.dumps({"structured_output": {"legitimate": True, "why": "ok"}})
    judge.ask("Waiting for the integration suite.", RUN, running=["uv run pytest -m integration"],
              runner=fake(out, seen))
    assert "STILL RUNNING" in seen["input"] and "uv run pytest -m integration" in seen["input"]


def test_without_background_commands_the_prompt_says_nothing_about_them():
    seen = {}
    out = json.dumps({"structured_output": {"legitimate": True, "why": "ok"}})
    judge.ask("Run complete: done.", RUN, runner=fake(out, seen))
    assert "STILL RUNNING" not in seen["input"]
