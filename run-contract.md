## Run contract (unattended runs)

Applies while a Foreman run is active (started with /foreman:run), until it ends.

- Follow the plan's order. Do not ask which unit comes next.
- Per unit: build, run /simplify on the changes (not in a plain run), run the project check, commit, update the resume point in the plan file, push the working branch.
- Never push a protected branch and never write to a client system without asking.
- A choice that is cheap to undo: make it, and add one line to the decisions log (what, why).
- A choice that is expensive to undo or client-facing: add it to the parked file and move to the next unit that does not depend on it.
- Ponytail's "does this need to exist?" questions follow those same two rules.
- Open and close your own tunnels. If the VPN is down, park the units that need it and continue with the rest.
- Before a test or a local service uses a localhost port, check that nothing else holds it (`lsof -nP -iTCP:<port> -sTCP:LISTEN`). A port a tunnel holds leads off this machine, possibly to production.
- Frontend change: open the changed screen in a browser, confirm it works, and save a screenshot to `.foreman/walkthrough/`.
- Stop only when the run is complete or everything left is blocked. Open the final message with `Run complete:`, `Blocked on you:` or `Decision needed:`.
- A chain of plans runs in order on stacked branches (each from the previous plan's tip, never through `main`); a blocked plan stops the chain.
- Never run /foreman:init or change `.foreman.toml` during a run; a stale audit goes in the hand-back.
- End every run with a hand-back: units done, decisions made, questions parked, check status, what you need from me.
- Hand each unit to a fresh subagent unless the plan says otherwise, one unit at a time, so the run's context stays small. Subagents run in the background: after launching one, end your turn with one line and wait. Its report wakes you, and the stop gate lets that stop through. Never poll its output or wait with `sleep`. You keep the bookkeeping: review its report, run the check, commit, update the resume point, push. Keep the plan's working directory. The check also runs on its own before each subagent starts; if it fails, the launch is refused with its output.
- A command you must wait for (the full test suite) runs in the foreground when it fits in 10 minutes. If you stop to wait for a background command, name it: its exit wakes you. A server or watcher never exits, so never stop to wait for one. macOS has no `timeout` command.
