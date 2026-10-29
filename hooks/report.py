"""Did Foreman work as intended? Checks each run in a project's gate log against what the gates promise.

The log holds structure only (steps, decisions, durations); so does this report. Read it, then paste it into an
issue if you like: it names no files, commands, branches or plans.
usage: uv run --no-project --python 3.12 report.py [project directory, default: here] [--since 2026-10-28]
"""
from __future__ import annotations

import argparse
import json
import platform
import statistics
import subprocess
import sys
from collections import Counter
from pathlib import Path

import declaration

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
OPENINGS = ("complete", "blocked", "decision")


def read_log(root: Path) -> list[dict]:
    try:
        lines = (root / ".foreman" / "gate.log").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            out.append(event)
    return out


def runs(events: list[dict]) -> list[list[dict]]:
    """Split at each run's start marker. A log from before the markers (Foreman 0.3.0) is one unmarked run."""
    starts = [i for i, e in enumerate(events) if e.get("step") == "run" and e.get("action") == "start"]
    if not starts:
        return [events] if events else []
    return [events[a:b] for a, b in zip(starts, starts[1:] + [len(events)])]


def _marked(run: list[dict]) -> bool:
    return bool(run) and run[0].get("step") == "run"


def invariants(run: list[dict]) -> list[tuple[str, str]]:
    """(status, line) per promise: ok, FAIL, or ? where the log is too old to tell."""
    out: list[tuple[str, str]] = []
    count = Counter((e.get("step"), e.get("action")) for e in run)

    stale = sum(n for (step, _), n in count.items() if step == "hooks")
    if stale:
        out.append(("FAIL", f"hooks loaded before an update: seen at {stale} stop(s); quit claude and start it again"))
    else:
        out.append(("ok", "hooks: loaded as installed") if _marked(run) else ("?", "hooks: this log is too old to show it"))

    seen: set[str] = set()
    decisions = launches = missing = 0
    logged = sum(e.get("step") == "unit_check" for e in run)
    for e in run:
        if e.get("step") == "unit_check":
            decisions += 1
        elif e.get("step") == "wait":
            for agent in e.get("subagents") or []:
                if agent in seen:
                    continue
                seen.add(agent)
                launches += 1
                if decisions:
                    decisions -= 1
                else:
                    missing += 1
    if not _marked(run):
        out.append(("?", f"unit gate: {logged} logged checks for {launches} subagent launches; "
                         "this log is too old to show its skips"))
    elif missing:
        out.append(("FAIL", f"unit gate: no decision before {missing} of {launches} subagent launches "
                            "(hooks loaded before an update?)"))
    else:
        out.append(("ok", f"unit gate: a decision before each of {launches} subagent launches"))

    gave_up = Counter(e.get("step") for e in run if e.get("action") == "gave_up")
    out.append(("FAIL", "gave up: " + ", ".join(f"{s} ({n})" for s, n in gave_up.items()))
               if gave_up else ("ok", "no gate gave up"))

    held = count[("declaration", "block")]
    if held:
        out.append(("FAIL", f"declaration changed during the run: held {held} time(s)"))

    unavailable = count[("judge", "unavailable")]
    out.append(("FAIL", f"judge unavailable at {unavailable} stop(s)") if unavailable
               else ("ok", "judge: available at every stop it was asked"))

    results = [e["ok"] for e in run if e.get("step") in ("check", "unit_check") and "ok" in e]
    if results and not results[-1]:
        out.append(("FAIL", "check: failing at the end of the run"))
    else:
        out.append(("ok", f"check: {'passing at the end' if results else 'not needed (tree unchanged)'}"))

    # The last hand-back counts if no work came after it: talk after a Blocked on you is not a new hand-back.
    work = [i for i, e in enumerate(run) if e.get("step") in ("wait", "unit_check", "check") or e.get("action") == "block"]
    handed = [(i, e["opening"]) for i, e in enumerate(run) if e.get("step") == "stop" and e.get("opening") in OPENINGS]
    if not _marked(run):
        out.append(("?", "hand-back: this log is too old to show it"))
    elif handed and handed[-1][0] > max(work, default=-1):
        out.append(("ok", f"hand-back: {handed[-1][1]}"))
    else:
        out.append(("FAIL", "no hand-back: the log ends without Run complete, Blocked on you or Decision needed"))
    return out


def summary(run: list[dict]) -> str:
    checks = [e for e in run if e.get("step") in ("check", "unit_check") and "ok" in e]
    seconds = [e["seconds"] for e in checks if isinstance(e.get("seconds"), (int, float))]
    blocks = Counter(e.get("step") for e in run if e.get("action") == "block")
    launches = len({a for e in run if e.get("step") == "wait" for a in e.get("subagents") or []})
    parts = [f"checks {len(checks)} ({sum(not e['ok'] for e in checks)} failed"
             + (f", median {statistics.median(seconds):.0f} s)" if seconds else ")"),
             f"stops {sum(e.get('step') == 'stop' for e in run)}",
             "blocks " + (", ".join(f"{s} {n}" for s, n in blocks.items()) or "0"),
             f"subagents {launches}"]
    return ", ".join(parts)


def versions() -> dict:
    try:
        foreman = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError):
        foreman = "?"
    try:
        claude = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=20).stdout.split()[0]
    except (OSError, subprocess.SubprocessError, IndexError):
        claude = "?"
    return {"foreman": foreman, "claude": claude, "os": f"{platform.system()} {platform.release()}"}


def render(root: Path, since: str = "", versions: dict | None = None) -> str:
    v = versions or globals()["versions"]()
    events = [e for e in read_log(root) if str(e.get("ts", "")) >= since]
    lines = [f"Foreman report: foreman {v['foreman']}, Claude Code {v['claude']}, {v['os']}"]
    found = runs(events)
    if not found:
        return lines[0] + "\nno gate log entries" + (f" since {since}" if since else "")
    for n, run in enumerate(found, 1):
        start = run[0]
        head = (f"Run {n}: started {start.get('ts', '?')}, {start.get('units', '?')} units" if _marked(run)
                else f"Run {n} (log from before run markers): {run[0].get('ts', '?')} to {run[-1].get('ts', '?')}")
        lines += ["", head]
        lines += [f"  {status:4} {line}" for status, line in invariants(run)]
        lines.append(f"  {summary(run)}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="report.py")
    ap.add_argument("project", nargs="?", type=Path, default=Path.cwd())
    ap.add_argument("--since", default="", help="only log entries from this time on, e.g. 2026-10-28T21:00")
    args = ap.parse_args(argv)
    root = declaration.find_root(args.project)
    if root is None:
        print(f"no {declaration.NAME} found in {args.project} or above", file=sys.stderr)
        return 1
    print(render(root, args.since))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
