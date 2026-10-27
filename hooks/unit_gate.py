"""PreToolUse hook on Agent: during a run, the project's check must pass before the next subagent starts.

The stop gate cannot hold units to the check: every stop between units is a wait for a subagent, with
the tree half-written. The moment before the next launch is the one point where the previous unit is
finished and nothing is writing to the tree.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import declaration
import gitutil
import hookio
import trust
from state import State
from stop_gate import MAX_ATTEMPTS, Outcome, _park, changed_during_run, pending_subagents, run_check


def evaluate(inp: dict, *, check=run_check, now: float | None = None) -> Outcome:
    if inp.get("tool_name") not in ("Agent", "Task") or inp.get("agent_id"):
        return Outcome("allow")  # not a launch, or a subagent's own subagents, which work inside its unit
    root = declaration.find_root(Path(inp.get("cwd") or os.getcwd()))
    if root is None or not gitutil.is_toplevel(root):
        return Outcome("allow")  # the stop gate and the doctor say why the gates are off
    st = State(root)
    if not st.run():
        return Outcome("allow")
    if not trust.is_trusted(root):  # its check is never run; the run waits until it is restored
        out = changed_during_run(st, "declaration_denials")
        return Outcome("deny", reason=out.reason) if out.action == "block" else out
    if pending_subagents(str(inp.get("transcript_path") or ""), time.time() if now is None else now):
        return Outcome("allow")  # another subagent is still writing to the tree
    s = st.read()
    if s.pop("declaration_denials", None) is not None:
        st.write(s)
    fp = gitutil.fingerprint(root)
    if fp in (s.get("passed_fingerprint"), s.get("check_ok_fingerprint")):
        return Outcome("allow")
    decl = declaration.load(root)
    t0 = time.monotonic()
    ok, tail = check(decl)
    st.log(step="unit_check", ok=ok, seconds=round(time.monotonic() - t0, 1))
    s = st.read()
    if ok:
        s["check_ok_fingerprint"] = gitutil.fingerprint(root)  # the check may have rewritten files
        s.pop("unit_denials", None)
        st.write(s)
        return Outcome("allow")
    reason = ("foreman: the project check fails, so the next unit cannot start yet. "
              "Fix it (or have the previous unit's subagent fix it), commit, then launch the next unit.\n"
              f"$ {decl.check_command}\n{tail}")
    denials = int(s.get("unit_denials", 0))
    if denials >= MAX_ATTEMPTS:
        s.pop("unit_denials", None)
        st.write(s)
        title = "CHECK FAILING before the next unit"
        recorded = (f"Recorded in {decl.parked}." if _park(decl, title, reason) else
                    f"It could not be recorded in {decl.parked} (a symlink, or outside the project).")
        st.log(step="unit_check", action="gave_up")
        return Outcome("allow", warning=f"foreman: {title} after {MAX_ATTEMPTS} attempts. {recorded}\n{tail}")
    s["unit_denials"] = denials + 1
    st.write(s)
    st.log(step="unit_check", action="deny")
    return Outcome("deny", reason=reason)


def main() -> int:
    if os.environ.get("FOREMAN_JUDGE"):
        return 0
    try:
        out = evaluate(hookio.read_input())
    except Exception as e:  # fail open and loud, like the stop gate: a broken gate must not trap the run
        hookio.emit({"systemMessage": f"foreman: unit gate crashed, subagent launched unchecked: {e!r}"})
        return 0
    if out.action == "deny":
        hookio.emit({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                            "permissionDecisionReason": out.reason}})
    elif out.warning:
        hookio.emit({"systemMessage": out.warning})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
