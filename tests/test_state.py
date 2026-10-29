import json

from state import State


def test_state_round_trip_and_missing_file(repo):
    st = State(repo)
    assert st.read() == {}
    st.write({"a": 1})
    assert st.read() == {"a": 1}


def test_corrupt_state_reads_as_empty(repo):
    st = State(repo)
    st.write({"a": 1})
    (st.dir / "state.json").write_text("{not json")
    assert st.read() == {}


def test_run_lifecycle_and_binding(repo):
    st = State(repo)
    assert st.run() is None
    st.start_run([("PLAN.md", ["W8", "W9"])], "abc")
    assert st.run() == {"plans": [{"file": "PLAN.md", "units": ["W8", "W9"]}], "units": ["W8", "W9"],
                        "start_head": "abc", "session_id": "", "plain": False, "plan_digests": {"PLAN.md": ""}}
    st.bind_run("s1")
    st.bind_run("s2")  # already bound: no change
    assert st.run()["session_id"] == "s1"
    st.end_run()
    assert st.run() is None


def test_log_appends_json_lines(repo):
    st = State(repo)
    st.log(step="check", ok=True)
    st.log(step="stop", action="allow")
    lines = [json.loads(x) for x in (st.dir / "gate.log").read_text().splitlines()]
    assert [x["step"] for x in lines] == ["check", "stop"]
    assert "ts" in lines[0]


def test_a_chain_keeps_its_plans_in_order(repo):
    st = State(repo)
    (repo / "A.md").write_text("a")
    st.start_run([("A.md", ["a1", "a2"]), ("B.md", ["b1"])], "abc")
    run = st.run()
    assert State.plan_files(run) == ["A.md", "B.md"]
    assert run["units"] == ["a1", "a2", "b1"]
    assert run["plan_digests"]["A.md"] == st.plan_digest("A.md") != ""


def test_a_run_from_before_chains_still_reads(repo):
    assert State.plan_files({"plan_file": "PLAN.md", "units": ["W8"]}) == ["PLAN.md"]


# The gates write here even when the declaration is not approved (a run held after it changed), so a
# repository must not be able to point these writes elsewhere with a symlink (security review, 2026-10-27).

import pytest


def test_a_symlinked_state_directory_is_refused(repo, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (repo / ".foreman").symlink_to(elsewhere)
    st = State(repo)
    with pytest.raises(OSError):
        st.write({"a": 1})
    with pytest.raises(OSError):
        st.log(step="x")
    assert list(elsewhere.iterdir()) == []


def test_symlinked_files_inside_are_not_followed(repo, tmp_path):
    target = tmp_path / "victim.txt"
    target.write_text("keep\n")
    st = State(repo)
    st.dir.mkdir()
    (st.dir / "gate.log").symlink_to(target)
    (st.dir / "state.json.tmp").symlink_to(target)
    with pytest.raises(OSError):
        st.log(step="x")
    st.write({"a": 1})
    assert target.read_text() == "keep\n"
    assert st.read() == {"a": 1}


def test_start_and_end_of_a_run_are_logged_without_content(repo):
    st = State(repo)
    st.start_run([("PLAN.md", ["W8", "W9"])], "abc")
    st.end_run("stopped")
    rows = [json.loads(x) for x in (st.dir / "gate.log").read_text().splitlines()]
    assert {"step": "run", "action": "start", "units": 2, "head": "abc"}.items() <= rows[0].items()
    assert {"step": "run", "action": "end", "how": "stopped"}.items() <= rows[1].items()
    assert "PLAN.md" not in (st.dir / "gate.log").read_text() and "W8" not in (st.dir / "gate.log").read_text()


def test_a_session_keeps_the_start_it_was_first_given(repo):
    """SessionStart fires again after compaction and on resume, with the same id: the first start must stay."""
    st = State(repo)
    st.session_started("s1", "aaa")
    st.session_started("s1", "bbb")
    assert st.session_head("s1") == "aaa"
