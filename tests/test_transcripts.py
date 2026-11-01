"""transcripts.py: a project's Claude Code transcripts inside a time window, read as events."""
import json
from pathlib import Path

import transcripts
from conftest import write_transcript
from transcript_entries import (blocked, launched, queued, ran, reported, result, said, stopped, told, typed)


def test_the_projects_folder_is_named_like_claude_code_names_it(tmp_path):
    assert transcripts.project_dir(Path("/x/My_App.v2"), tmp_path) == tmp_path / "-x-My-App-v2"


def test_sessions_started_in_a_subfolder_count_and_a_sibling_repository_does_not(tmp_path):
    root = tmp_path / "work" / "app"
    (root / "platform").mkdir(parents=True)
    base = tmp_path / "projects"
    own, sub = transcripts.project_dir(root, base), transcripts.project_dir(root / "platform", base)
    sibling = transcripts.project_dir(tmp_path / "work" / "app-old", base)
    for folder, cwd in ((own, root), (sub, root / "platform"), (sibling, tmp_path / "work" / "app-old")):
        write_transcript(folder / "s.jsonl", [{**said("2026-10-29T11:00:00.000Z", folder.name), "cwd": str(cwd)}])
    assert transcripts.project_dirs(root, base) == sorted([own, sub, sibling])
    entries, _ = transcripts.read_window(transcripts.project_dirs(root, base), "2026-10-29T10:00:00.000Z",
                                         "2026-10-29T12:00:00.000Z", root)
    assert sorted(e.file.parent for e in entries) == sorted([own, sub])


def test_gate_log_times_are_local_and_transcript_times_utc(cet):
    assert transcripts.to_utc("2026-10-29T12:01:50") == "2026-10-29T11:01:50.000Z"


def test_the_window_takes_sessions_and_their_subagents_and_leaves_out_programs(tmp_path):
    write_transcript(tmp_path / "s1.jsonl", [said("2026-10-29T10:00:00.000Z", "before"),
                                             said("2026-10-29T11:00:00.000Z", "inside")])
    write_transcript(tmp_path / "s1" / "subagents" / "a.jsonl", [said("2026-10-29T11:10:00.000Z", "sub")])
    write_transcript(tmp_path / "s2.jsonl", [said("2026-10-29T11:20:00.000Z", "a new session in the run")])
    review = said("2026-10-29T11:05:00.000Z", "security review")
    review["entrypoint"] = "sdk-py"
    write_transcript(tmp_path / "s3.jsonl", [review])
    (tmp_path / "s2.jsonl").write_text((tmp_path / "s2.jsonl").read_text() + "not json\n")
    entries, broken = transcripts.read_window([tmp_path], "2026-10-29T10:30:00.000Z", "2026-10-29T12:00:00.000Z")
    texts = [e.data["message"]["content"][0]["text"] for e in entries]
    assert texts == ["inside", "sub", "a new session in the run"]
    assert [e.sub for e in entries] == [False, True, False]
    assert broken == 1


def kinds(entries, tmp_path):
    write_transcript(tmp_path / "s1.jsonl", entries)
    found, _ = transcripts.read_window([tmp_path], "2026-01-01T00:00:00.000Z", "2027-01-01T00:00:00.000Z")
    return transcripts.events(found)


def test_a_stop_carries_the_closing_message_and_a_block_its_reason(tmp_path):
    evs = kinds([said("2026-10-29T11:00:00.000Z", "Task 1 is with a subagent."), stopped("2026-10-29T11:00:01.000Z"),
                 typed("2026-10-29T11:00:02.000Z", "Stop hook feedback:\nWaiting is not a hand-back."),
                 blocked("2026-10-29T11:00:03.000Z", "Frontend files changed: a.tsx")], tmp_path)
    assert [(e.kind, e.text) for e in evs] == [("stop", "Task 1 is with a subagent."),
                                               ("block", "Waiting is not a hand-back.")]


def test_a_block_is_one_event_in_either_order_and_the_next_turn_counts_again(tmp_path):
    evs = kinds([blocked("2026-10-29T11:00:00.000Z", "Frontend files changed."),
                 typed("2026-10-29T11:00:00.100Z", "Stop hook feedback:\nFrontend files changed, in other words."),
                 said("2026-10-29T11:01:00.000Z", "Looking."),
                 typed("2026-10-29T11:02:00.000Z", "Stop hook feedback:\nStill blocked."),
                 blocked("2026-10-29T11:02:00.100Z", "Still blocked, in other words.")], tmp_path)
    assert [e.text for e in evs if e.kind == "block"] == ["Frontend files changed.", "Still blocked."]


def test_a_report_or_a_message_queued_while_the_session_was_busy_is_seen(tmp_path):
    report = "<task-notification>\n<task-id>a1</task-id>\n<status>completed</status>\n</task-notification>"
    evs = kinds([queued("2026-10-29T11:00:00.000Z", report), said("2026-10-29T11:01:00.000Z", "Read it."),
                 queued("2026-10-29T11:02:00.000Z", "should I stop?", mode="prompt")], tmp_path)
    assert [(e.kind, e.seconds) for e in evs] == [("report", 60), ("user", None)]
    assert evs[1].text == "should I stop?"


def test_a_task_notification_message_is_a_report(tmp_path):
    evs = kinds([typed("2026-10-29T11:00:00.000Z", "<task-notification>done</task-notification>", isMeta=True)], tmp_path)
    assert [e.kind for e in evs] == ["report"]


def test_the_window_includes_both_of_its_bounds(tmp_path):
    write_transcript(tmp_path / "s1.jsonl", [said("2026-10-29T10:59:59.999Z", "before"), said("2026-10-29T11:00:00.000Z", "a"),
                                             said("2026-10-29T12:00:00.000Z", "b"), said("2026-10-29T12:00:00.001Z", "after")])
    entries, _ = transcripts.read_window([tmp_path], "2026-10-29T11:00:00.000Z", "2026-10-29T12:00:00.000Z")
    assert [e.data["message"]["content"][0]["text"] for e in entries] == ["a", "b"]


def test_lines_of_the_wrong_shape_do_not_stop_the_reading(tmp_path):
    evs = kinds([said("2026-10-29T11:00:00.000Z", "fine")], tmp_path)  # writes the session
    path = tmp_path / "s1.jsonl"
    odd = {"timestamp": "2026-10-29T11:00:01.000Z", "type": "assistant", "message": {"content": [{"type": "text", "text": 7}]}}
    path.write_text('[1, 2]\n"cwd entrypoint"\n' + json.dumps(odd) + "\n" + path.read_text())
    entries, broken = transcripts.read_window([tmp_path], "2026-01-01T00:00:00.000Z", "2027-01-01T00:00:00.000Z")
    assert len(entries) == 2 and broken == 0 and [e.kind for e in transcripts.events(entries)] == []


def test_a_line_that_only_mentions_the_key_does_not_decide_the_session(tmp_path):
    mention = {"message": {"content": 'the "cwd" and "entrypoint" keys'}}
    (tmp_path / "s.jsonl").write_text('["cwd", "entrypoint"]\n' + json.dumps(mention) + "\n"
                                      + json.dumps({"cwd": str(tmp_path), "entrypoint": "sdk-py"}) + "\n")
    assert transcripts._inside(tmp_path / "s.jsonl", tmp_path) and transcripts._headless(tmp_path / "s.jsonl")


def test_a_subagent_report_records_when_the_session_acted_on_it(tmp_path):
    evs = kinds([launched("2026-10-29T11:00:00.000Z", "Task 1"), reported("2026-10-29T11:10:00.000Z"),
                 said("2026-10-29T11:15:00.000Z", "Task 1 is committed.")], tmp_path)
    assert [(e.kind, e.text) for e in evs][0] == ("launch", "Task 1")
    assert evs[1].kind == "report" and evs[1].seconds == 300


def test_commands_carry_their_duration_and_a_denial_is_told_from_an_error(tmp_path):
    evs = kinds([ran("2026-10-29T11:00:00.000Z", "make check", id="t1"),
                 result("2026-10-29T11:06:00.000Z", id="t1"),
                 ran("2026-10-29T11:07:00.000Z", "git push origin main", id="t2", run_in_background=True),
                 result("2026-10-29T11:07:01.000Z", id="t2", error=True,
                        text="The user doesn't want to proceed with this tool use."),
                 ran("2026-10-29T11:08:00.000Z", "false", id="t3"),
                 result("2026-10-29T11:08:01.000Z", id="t3", error=True, text="Exit code 1")], tmp_path)
    bash = [e for e in evs if e.kind == "bash"]
    assert [(e.command, e.seconds, e.background) for e in bash] == [("make check", 360, False),
                                                                   ("git push origin main", 1, True),
                                                                   ("false", 1, False)]
    assert [(e.kind, e.command) for e in evs if e.kind in ("denied", "error")] == [
        ("denied", "git push origin main"), ("error", "false")]


def test_the_users_messages_and_foremans_are_kept_and_command_wrappers_are_not(tmp_path):
    evs = kinds([typed("2026-10-29T11:00:00.000Z", "<command-name>/foreman:run</command-name>"),
                 typed("2026-10-29T11:01:00.000Z", "should I approve the migration?"),
                 told("2026-10-29T11:02:00.000Z", "foreman: Claude Code loaded Foreman's hooks before they last changed")],
                tmp_path)
    assert [(e.kind, e.text[:20]) for e in evs] == [("user", "should I approve the"), ("hook", "foreman: Claude Code")]


def test_a_line_separator_inside_a_message_does_not_split_the_entry(tmp_path):
    # Node's JSON.stringify leaves U+2028, U+2029 and NEL raw; str.splitlines() would split on them.
    text = "a" + chr(0x2028) + "b" + chr(0x2029) + "c" + chr(0x85) + "d"
    (tmp_path / "s1.jsonl").write_text(json.dumps(said("2026-10-29T11:00:00.000Z", text), ensure_ascii=False) + "\n",
                                       encoding="utf-8")
    entries, broken = transcripts.read_window([tmp_path], "2026-10-29T10:00:00.000Z", "2026-10-29T12:00:00.000Z")
    assert len(entries) == 1 and broken == 0
    assert entries[0].data["message"]["content"][0]["text"] == text
