"""Ask a small model whether an unattended session stopped for a legitimate reason."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass

OPENINGS = ("decision needed", "blocked on you", "run complete")

SYSTEM = """\
You judge whether a coding agent, working unattended through planned units, stopped for a \
legitimate reason. You see its final message and the user's latest prompt.

Legitimate stops:
1. A decision that is expensive to undo or client-facing, which only the user can make.
2. Blocked on something only the user can do: VPN, credentials, access, a manual step, or \
permission for a write to a client system or a push to a protected branch, when nothing else \
in the run can proceed without it.
3. Every unit of the run is complete.
4. The message is a direct answer to a question in the user's latest prompt.
5. It reports that the project's check still fails after repeated attempts and that the \
remaining units depend on the failing one.
6. It waits for a command listed as still running in the background that ends on its own (a \
test suite, a build): the agent is woken when it exits. Waiting for a server, a watcher or \
anything else that keeps running is not legitimate, because nothing would wake it.

Not legitimate:
- Asking which unit or task to do next.
- Asking permission to continue, commit, push the working branch, or clean up.
- Announcing what it will do next and waiting for "go".
- Offering optional extra work as a question.
- Stopping with units remaining and nothing blocking.

A legitimate stop normally opens with "Decision needed:", "Blocked on you:" or "Run complete:". \
A message without that opening which clearly fits a legitimate reason is still legitimate. \
A message with the opening whose content is really a sequencing or housekeeping question is not.

Reply using the JSON schema. Keep "why" to one sentence addressed to the agent."""

SCHEMA = json.dumps({
    "type": "object",
    "properties": {"legitimate": {"type": "boolean"}, "why": {"type": "string"}},
    "required": ["legitimate", "why"],
})


@dataclass(frozen=True)
class Verdict:
    legitimate: bool
    why: str


class JudgeUnavailable(Exception):
    pass


def _clip(text: str, head: int = 1500, tail: int = 4500) -> str:
    return text if len(text) <= head + tail else text[:head] + "\n[...]\n" + text[-tail:]


def _plans(run: dict) -> str:
    plans = run.get("plans") or [{"file": run.get("plan_file", ""), "units": run.get("units", [])}]
    return "; ".join(f"{p['file']}: {', '.join(p['units'])}" for p in plans)


def ask(message: str, run: dict, last_prompt: str = "", running=(), *, model: str | None = None,
        timeout: int = 60, runner=subprocess.run) -> Verdict:
    model = model or os.environ.get("FOREMAN_JUDGE_MODEL", "haiku")
    prompt = (
        f"UNITS IN THIS RUN: {', '.join(run.get('units', []))}\n"
        f"PLANS, IN ORDER (one run works through all of them): {_plans(run)}\n\n"
        f"USER'S LATEST PROMPT:\n{_clip(last_prompt, 500, 1000)}\n\n"
        f"AGENT'S FINAL MESSAGE:\n{_clip(message)}"
    )
    if running:
        prompt += "\n\nBACKGROUND COMMANDS STILL RUNNING:\n" + "\n".join(f"- {c[:300]}" for c in running)
    cmd = [
        "claude", "-p", "--model", model, "--tools", "", "--no-session-persistence",
        "--disable-slash-commands", "--strict-mcp-config", "--output-format", "json",
        "--setting-sources", "user",
        "--settings", '{"disableAllHooks":true}', "--system-prompt", SYSTEM,
        "--json-schema", SCHEMA,
    ]
    try:
        # A fresh private directory: nothing another process planted there can be loaded as
        # project settings or instructions.
        with tempfile.TemporaryDirectory(prefix="foreman-judge-") as cwd:
            p = runner(cmd, input=prompt, capture_output=True, text=True, timeout=timeout,
                       cwd=cwd, env={**os.environ, "FOREMAN_JUDGE": "1"})
        out = json.loads(p.stdout)["structured_output"]
        return Verdict(bool(out["legitimate"]), str(out["why"]))
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError) as e:
        raise JudgeUnavailable(repr(e)) from e
