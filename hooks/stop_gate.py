"""Stop hook: a session may stop only when the project's check passes."""
from __future__ import annotations

import calendar
import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import declaration
import gitutil
import hookio
import judge
import trust
from state import State

MAX_ATTEMPTS = 3
TAIL_LINES = 40
# A subagent that has not reported back after this long is presumed lost, and stops are gated again.
SUBAGENT_PATIENCE_SECONDS = 3 * 3600
LAUNCHED = re.compile(r"Async agent launched.*?agentId: (\w+)", re.S)
AGENT_TOOLS = ("Agent", "Task")  # Task: the tool's name before Claude Code 2.1
NOTIFIED = re.compile(r"<task-notification>\s*<task-id>(\w+)</task-id>")


@dataclass
class Outcome:
    action: str  # "allow" or "block"
    reason: str = ""
    warning: str = ""


def run_check(decl: declaration.Declaration) -> tuple[bool, str]:
    proc = subprocess.Popen(
        decl.check_command, shell=True, cwd=decl.root, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
    )
    try:
        out, _ = proc.communicate(timeout=decl.check_timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        return False, f"check timed out after {decl.check_timeout}s"
    return proc.returncode == 0, "\n".join(out.splitlines()[-TAIL_LINES:])


def _save_counts(st: State, session_id: str, counts: dict) -> None:
    s = st.read()
    s.setdefault("attempts", {})[session_id] = counts
    st.write(s)


def _park(decl: declaration.Declaration, title: str, detail: str) -> bool:
    """Append an entry to the parked file. False if that file is a symlink or outside the project."""
    if not declaration.stays_inside(decl.root, decl.parked):
        return False
    try:
        fd = os.open(decl.root / decl.parked,
                     os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(f"\n## {time.strftime('%Y-%m-%d %H:%M')} {title}\n\n```\n{detail}\n```\n")
        return True
    except OSError:
        return False


def last_user_prompt(transcript_path: str) -> str:
    """The newest prompt the user typed, read from the session transcript. "" if unknown."""
    try:
        lines = Path(transcript_path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    for line in reversed(lines[-400:]):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("type") != "user" or rec.get("promptSource") != "typed":
            continue
        content = (rec.get("message") or {}).get("content")
        if isinstance(content, str):
            return content.strip()
        return "\n".join(c.get("text", "") for c in content or []
                         if isinstance(c, dict) and c.get("type") == "text").strip()
    return ""


def _records(transcript_path: str):
    try:
        with open(transcript_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(_text(c.get("content", c.get("text", ""))) for c in content if isinstance(c, dict))
    return ""


def pending_subagents(transcript_path: str, now: float) -> list[str]:
    """Subagents this session launched that have not reported back yet.

    Claude Code runs every subagent in the background: the Agent call returns at once and a
    task-notification wakes the session when the subagent finishes. Until then, stopping is how
    the session waits; blocking that stop only leaves it a sleep loop (pilot runs, October 2026).
    """
    if not transcript_path:
        return []
    started: dict[str, float] = {}
    agent_calls: set[str] = set()  # only the result of a real Agent call counts, not text that looks like one
    for rec in _records(transcript_path):
        content = (rec.get("message") or {}).get("content")
        if rec.get("type") == "queue-operation":
            for agent in NOTIFIED.findall(str(rec.get("content", ""))):
                started.pop(agent, None)
        elif rec.get("type") == "assistant" and isinstance(content, list):
            agent_calls |= {str(c.get("id")) for c in content if isinstance(c, dict)
                            and c.get("type") == "tool_use" and c.get("name") in AGENT_TOOLS}
        elif rec.get("type") == "user" and isinstance(content, list):
            for c in content:
                if not (isinstance(c, dict) and c.get("type") == "tool_result" and c.get("tool_use_id") in agent_calls):
                    continue
                for agent in LAUNCHED.findall(_text(c.get("content"))):
                    try:
                        started[agent] = calendar.timegm(time.strptime(rec["timestamp"][:19], "%Y-%m-%dT%H:%M:%S"))
                    except (KeyError, ValueError):
                        started[agent] = now
    return [a for a, t0 in started.items() if now - t0 < SUBAGENT_PATIENCE_SECONDS]


def running_subagents(inp: dict, now: float) -> list[str]:
    """Subagents still working. Claude Code lists them in the Stop input (`background_tasks`, seen in 2.1.310);
    older versions do not, and then the transcript is read instead."""
    tasks = inp.get("background_tasks")
    if isinstance(tasks, list):
        return [str(t.get("id", "")) for t in tasks
                if isinstance(t, dict) and t.get("type") == "subagent" and t.get("status") == "running"]
    return pending_subagents(str(inp.get("transcript_path") or ""), now)


def running_commands(inp: dict) -> list[str]:
    """Shell commands still running in the background, as the Stop input lists them."""
    tasks = inp.get("background_tasks")
    if not isinstance(tasks, list):
        return []
    return [str(t.get("command", "")) for t in tasks
            if isinstance(t, dict) and t.get("type") == "shell" and t.get("status") == "running"]


CHANGED = (f"foreman: {declaration.NAME} changed since you approved it, so the gates cannot trust it (a git checkout, "
           "reset or pull can change it without naming it). Restore the approved version (`git log -- "
           f"{declaration.NAME}`, then `git checkout <commit> -- {declaration.NAME}`) and carry on, or stop with "
           "'Blocked on you:' so the user can review and approve it.")


def changed_during_run(st: State, counter: str, fresh: bool = False) -> Outcome:
    """A run is active but its declaration no longer matches the approval: hold the session instead of letting
    the gates go quietly off. The session cannot approve it itself: the guard asks before trust.py approve."""
    return hold(st, counter, "declaration", CHANGED,
                f"DECLARATION CHANGED during the run and not restored after {MAX_ATTEMPTS} attempts: "
                f"the gates are off until {declaration.NAME} is approved again.", fresh)


def hold(st: State, counter: str, step: str, reason: str, gave_up: str, fresh: bool = False) -> Outcome:
    """Block a run's stop for something only the user can put right; after MAX_ATTEMPTS let go, loudly."""
    s = st.read()
    n = 0 if fresh else int(s.get(counter, 0))
    if n >= MAX_ATTEMPTS:
        s[counter] = 0
        st.write(s)
        st.log(step=step, action="gave_up")
        return Outcome("allow", warning=f"foreman: {gave_up}")
    s[counter] = n + 1
    st.write(s)
    st.log(step=step, action="block")
    return Outcome("block", reason=reason)


def _opening(message: str) -> str:
    m = message.lstrip().lower()
    return next((k for k, p in (("complete", "run complete"), ("blocked", "blocked on you"),
                                ("decision", "decision needed")) if m.startswith(p)), "other")


def _stale(inp: dict, now: float) -> Outcome | None:
    """Hooks loaded before they last changed: during a run, hold it until the session hands back to the user."""
    root = declaration.find_root(Path(inp.get("cwd") or os.getcwd()))
    if root is None or not trust.is_trusted(root) or not gitutil.is_toplevel(root):
        return None
    st = State(root)
    if not st.run():
        st.log(step="hooks", action="stale")
        return None
    if _opening(str(inp.get("last_assistant_message") or "")) in ("blocked", "decision") or running_subagents(inp, now):
        return None
    return hold(st, "stale_blocks", "hooks", (
        f"foreman: {hookio.STALE}. The run cannot go on without its gates. Stop now with 'Blocked on you:' "
        "and ask the user to restart claude and continue the run."),
        f"STALE HOOKS: {hookio.STALE}. Let go after {MAX_ATTEMPTS} attempts.", fresh=not inp.get("stop_hook_active"))


def _walkthrough_is_fresh(st: State, root: Path, frontend_files: list[str]) -> bool:
    edits = [(root / f).stat().st_mtime for f in frontend_files if (root / f).is_file()]
    shots = [p.stat().st_mtime for p in st.walkthrough.glob("*.png")]
    return not edits or (bool(shots) and max(shots) > max(edits))


def evaluate(inp: dict, *, check=run_check, ask=judge.ask, now: float | None = None, stale: bool = False) -> Outcome:
    if stale:
        held = _stale(inp, time.time() if now is None else now)
        if held:
            return held
    out = _evaluate(inp, check=check, ask=ask, now=now)
    if stale and out.action == "allow":
        out = Outcome("allow", warning="\n".join(filter(None, [out.warning, f"foreman: {hookio.STALE}."])))
    return out


def _evaluate(inp: dict, *, check, ask, now: float | None) -> Outcome:
    root = declaration.find_root(Path(inp.get("cwd") or os.getcwd()))
    if root is None:
        return Outcome("allow")
    if not trust.is_trusted(root):  # an unapproved declaration is never executed
        unapproved = Outcome("allow", warning=(
            f"foreman: {declaration.NAME} is new or changed and not approved, so the gates are off "
            "for this project. Read it, then approve it with trust.py."))
        st = State(root)
        message = str(inp.get("last_assistant_message") or "").lstrip().lower()
        if not st.run() or message.startswith(("blocked on you", "decision needed")):
            return unapproved
        if running_subagents(inp, time.time() if now is None else now):  # a wait; the stop after the report holds
            return unapproved
        return changed_during_run(st, "declaration_blocks", fresh=not inp.get("stop_hook_active"))
    if not gitutil.is_toplevel(root):
        return Outcome("allow", warning=(
            f"foreman: {declaration.NAME} is not at the git top level, so the gates are off. "
            "Move it to the top of the repository."))
    decl = declaration.load(root)
    st = State(root)
    waiting = running_subagents(inp, time.time() if now is None else now)
    if waiting:  # the tree is half-written and the session will be woken: nothing to check or judge yet
        st.log(step="wait", subagents=waiting)
        return Outcome("allow")
    s = st.read()
    sid = str(inp.get("session_id", ""))
    # Blocks are counted per step, so the judge's nudges never use up the check's attempts.
    saved = s.get("attempts", {}).get(sid)
    counts = dict(saved) if isinstance(saved, dict) and inp.get("stop_hook_active") else {}
    head_now = gitutil.head(root)
    if counts.get("judge_head") != head_now:
        counts.pop("judge", None)  # a commit landed since the last nudge: the run is progressing

    def fail(step: str, reason: str) -> Outcome:
        if counts.get(step, 0) >= MAX_ATTEMPTS:
            counts[step] = 0
            _save_counts(st, sid, counts)
            title = "CHECK FAILING" if step == "check" else f"GATE NOT SATISFIED ({step})"
            recorded = (f"Recorded in {decl.parked}." if _park(decl, title, reason) else
                        f"It could not be recorded in {decl.parked} (a symlink, or outside the project).")
            st.log(step=step, action="gave_up")
            return Outcome("allow", warning=(
                f"foreman: {title} after {MAX_ATTEMPTS} attempts. {recorded}\n{reason}"))
        counts[step] = counts.get(step, 0) + 1
        if step == "judge":
            counts["judge_head"] = head_now
        _save_counts(st, sid, counts)
        st.log(step=step, action="block")
        return Outcome("block", reason=reason)

    fp = gitutil.fingerprint(root)
    if fp != s.get("passed_fingerprint"):
        if fp != s.get("check_ok_fingerprint"):
            t0 = time.monotonic()
            ok, tail = check(decl)
            st.log(step="check", ok=ok, seconds=round(time.monotonic() - t0, 1))
            if not ok:
                return fail("check", (
                    "The project check failed. Fix it before you stop.\n"
                    f"$ {decl.check_command}\n{tail}"))
            fp = gitutil.fingerprint(root)  # the check may have rewritten files
            s = st.read()
            s["check_ok_fingerprint"] = fp
            st.write(s)
        s = st.read()
        run = st.run()
        # Measure from the newest of: the last passed stop, the run's start, this session's start. Commits made
        # while the gates were off belong to none of them.
        since = gitutil.newest(root, [s.get("passed_head", ""), (run or {}).get("start_head", ""),
                                      st.session_head(sid)])
        frontend = [f for f in gitutil.changed_files(root, since)
                    if decl.frontend_paths and f.startswith(decl.frontend_paths)]
        if frontend and not _walkthrough_is_fresh(st, root, frontend):
            return fail("walkthrough", (
                "Frontend files changed: " + ", ".join(frontend[:10]) + ". "
                "Open each changed screen in a browser, confirm it works, and save a "
                "screenshot as .foreman/walkthrough/<name>.png before you stop."))
        if run:
            plans = State.plan_files(run)
            changed = gitutil.changed_files(root, since)
            digests = run.get("plan_digests", {})
            moved = any(f in changed or st.plan_digest(f) != digests.get(f, "") for f in plans)
            if since and since != gitutil.head(root) and not moved:
                return fail("resume", (
                    f"Commits landed but the plan ({', '.join(plans)}) did not change. "
                    "Update the resume point of the plan you are working on before you stop."))
        s["passed_fingerprint"] = fp
        s["passed_head"] = gitutil.head(root)
        st.write(s)
        st.plan_passed()
    for step in ("check", "walkthrough", "resume"):
        counts.pop(step, None)

    st.bind_run(sid)
    run = st.run()
    if run and run.get("session_id") == sid:
        message = str(inp.get("last_assistant_message") or "")
        try:
            v = ask(message, run, last_user_prompt(str(inp.get("transcript_path") or "")), running_commands(inp))
        except judge.JudgeUnavailable as e:
            st.log(step="judge", action="unavailable")
            return Outcome("allow", warning=f"foreman: judge unavailable ({e}); this stop was not judged")
        if not v.legitimate:
            return fail("judge", (
                f"A run is active ({', '.join(run['units'])}). {v.why} "
                "Do not stop for sequencing or housekeeping: follow the plan's order, commit, "
                "push the working branch and continue with the next unblocked unit. "
                "If you must stop, open with 'Decision needed:', 'Blocked on you:' or 'Run complete:'."))
        if message.lstrip().lower().startswith("run complete"):
            st.end_run("complete")

    _save_counts(st, sid, {})
    st.log(step="stop", action="allow", **({"opening": _opening(str(inp.get("last_assistant_message") or ""))}
                                           if run else {}))
    return Outcome("allow")


def main() -> int:
    if os.environ.get("FOREMAN_JUDGE"):
        return 0
    try:
        out = evaluate(hookio.read_input(), stale=hookio.stale(sys.argv[1:]))
    except Exception as e:  # fail open and loud: a broken gate must not trap the session
        hookio.emit({"systemMessage": f"foreman: stop gate crashed, stop allowed unverified: {e!r}"})
        return 0
    if out.action == "block":
        hookio.emit({"decision": "block", "reason": out.reason})
    elif out.warning:
        hookio.emit({"systemMessage": out.warning})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
