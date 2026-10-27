# Foreman

A foreman for unattended Claude Code runs.

**Give Claude Code a plan, walk away, and come back to finished work: every unit built, checked and committed, with a report of what it decided and what it needs from you.** Not to a "shall I continue?" it asked five minutes after you left.

Claude Code can already do hours of planned work on its own. What stops it is that it hands control back too early: it asks which task is next, asks before committing, and says "done" without proof. Foreman takes that away. The session cannot stop until the project check passes and the plan has moved, and a second model rejects any stop that is not a real decision or a real blocker. It is built from Claude Code hooks, because instructions in `CLAUDE.md` alone proved too easy to drift from.

Use it when you have a reviewed plan, a project check that fails when something is broken, and work that needs your local machine. Skip it for exploratory work, where the questions are the point.

Status: a personal tool shared as is, under the MIT licence (`LICENSE`). Seven runs so far, about 60 units, all by one person on two projects. Tested with Claude Code 2.1.310; hooks depend on Claude Code's behaviour, so a later version may break something. Issues and ideas are welcome; support is not promised.

---

## Why it exists

Without Foreman, the session decides when it is done, and it decides too often and too early. With Foreman, a stop has to be earned: the project check passes, the plan has moved, and a second model agrees the reason for stopping is one only you can resolve.

### The problem, measured

It started from measuring how we actually worked, over 18 days of Claude Code sessions on a client project:

- 46 hours at the desk, a prompt needed every 5.5 minutes, never more than one session at a time
- 44% of that time was us reading and typing, not the model working
- of 73 long turns, 28 ended with a sequencing or housekeeping question ("X or Y next?", "shall I commit?", "say the word") that we answered with "go"; another 14 ended with us reviewing a result

Permission prompts were not the cause; the sessions already ran in `auto` mode. The model could do the work. It kept handing control back. Rules against that in `CLAUDE.md` held for a while and then drifted, which is why every rule a program can check is a hook here, and prose is kept for judgement a hook cannot make.

The scarce resource is your attention, not the model's ability or tokens. The aim is to start a run, leave, and come back to a report.

### Why not something that already exists

| Option | What it solves | Why it was not enough for us |
|--------|----------------|------------------------------|
| `auto` mode, skipping permissions | Permission prompts | Not the problem, as measured above |
| Prompt-level orchestration (plan-execution skills, workflow frameworks) | The plan, and "do not check in between tasks" | It is prose, the same soft rule that drifted. Foreman runs underneath these: it uses their plans and adds the hard gate |
| "Keep going" loops (a Stop hook that feeds the prompt back until a completion phrase appears) | Stopping too early | Nothing checks that "done" is true, a legitimate stop cannot be told from a lazy one, and there are no per-unit commits or decision records. The closest relative: the same mechanism without the verification |
| Cloud coding agents | Unattended work, real isolation, parallel runs, pull requests | They cannot reach the local environment our units need: VPN, SSH tunnels, a local stack in Docker Compose, a browser on the local dev server. They also decide "done" themselves |
| CI as the gate | Proof after a push | It catches the problem after the session has ended and lost its context. Foreman enforces the check while the session can still fix it |

### What it adds

1. **A stop has to be earned.** Check, resume point, screenshot of changed screens, then the judge. Cheap decisions are made and logged, expensive ones are parked, and the run moves to the next unit.
2. **A gate that has never failed proves nothing.** `/foreman:init` compares the check with everything the repository has, and proves with a canary test that each suite can actually turn it red. On the first project, the audit found that `make check` did not run the frontend tests at all.
3. **A hand-back you can review later.** Commits per unit, a decisions log, parked questions and a fixed opening line. You come back to a report, not a chat history.
4. **It cannot switch itself off quietly.** Approved declarations, a guard on its own files, and a doctor at every session start.

### What to expect, honestly

- **The evidence is thin.** Seven runs, one person, no control group. So far: every stop-gate block was resolved by the session without help, and the main context grew by about 50k to 80k tokens for 6 to 8 units (about 106k for the 9-unit run). Whether it reduces time at the desk has not been measured yet.
- **Running locally cuts both ways.** The access that makes local runs worth it (tunnels, the real stack) also means no isolation; see "What it does not protect" below.
- **Parts of it will become redundant** as Claude Code and the models improve. The verification parts (the earned stop, the proven check) should last longest.

---

## What you get

| Part | What it does |
|------|--------------|
| Stop gate (`Stop` hook) | While a run is active, refuses to let the session stop until the project check passes, changed frontend screens have a screenshot, the plan's resume point has moved, and a small model (Haiku) agrees the stop is legitimate: run complete, blocked on you, or a decision only you can make. Gives up after 3 tries per step and records why. |
| Unit gate (`PreToolUse` hook on `Agent`) | While a run is active, runs the project check before the next subagent starts, the one moment the previous unit is finished and nothing writes to the tree. A failing check refuses the launch with its output; after 3 refusals it lets the launch go and records why. |
| Guard (`PreToolUse` hook) | Forces a permission prompt before a push to a protected branch, a merge of a pull request, `git clean -x`, switching off or removing a plugin, or any edit of Foreman's own files. It never blocks on its own; it asks you. It also covers subagents' tool calls. |
| Doctor (`SessionStart` hook) | At every session start and after compaction, says whether Foreman is intact for this project and, during a run, which units are left and the run contract again. |
| `/foreman:init` skill | Audits a repository's checks, proves each test suite can fail (canary test), proposes fixes and writes `.foreman.toml`. |
| `/foreman:run` skill | Starts a run on one plan, or a chain of plans on stacked branches. Each unit goes to a fresh subagent so the main context stays small. |
| Run contract | The rules a session follows during a run (commit per unit, decisions log, parked file, hand-back). It enters the conversation only when a run starts, so sessions without a run never carry it. |

---

## Requirements

- Claude Code with the `claude` CLI on `PATH` (the judge runs `claude -p --model haiku` on each stop during a run, on your plan or API key)
- [uv](https://docs.astral.sh/uv/) and Python 3.12 (uv fetches it if needed)
- git; macOS or Linux
- A project with a check command that is fast enough to run after every unit and that actually fails when something is broken (`make check`, `npm test`, ...). Without one Foreman has little to hold the session to.
- Plans with numbered units and a resume point, for example from the superpowers `writing-plans` skill.

---

## Install and uninstall

It is a Claude Code plugin. This repository is both the plugin and a one-plugin marketplace. From GitHub:

    claude plugin marketplace add michalzan/foreman
    claude plugin install foreman@michalzan

or from a clone, with `claude plugin marketplace add ~/path/to/foreman` as the first line. Start a new session afterwards.

To update: from GitHub, `claude plugin marketplace update michalzan` and then `claude plugin update foreman@michalzan`; from a clone, `git pull` is enough, because a marketplace added from a local path is loaded in place. Either way the next session uses the new version. A GitHub install only updates when the `version` in `.claude-plugin/plugin.json` goes up, so releases are deliberate and a half-finished commit does not reach your sessions.

To remove it:

    claude plugin uninstall foreman@michalzan
    claude plugin marketplace remove michalzan

Nothing is written into your `~/.claude/settings.json` hooks or your `CLAUDE.md`; Claude Code records the plugin under `enabledPlugins` and `~/.claude/plugins/`. In a project without an approved `.foreman.toml` every gate is off and the session works as before. Your approvals (`~/.claude/foreman-trusted.json`) and every project's files stay when you uninstall.

Upgrading from the version before the plugin, when Foreman was called the harness and was installed with `install.py`? Remove that copy first, or its hooks run beside the plugin's (the doctor says so):

    uv run --python 3.12 uninstall.py

A project set up before 2026-10-27 has the old file names (`.harness.toml`, `.harness/`), and Foreman leaves its gates off there; the doctor says so. Move them once per project, commit the rename, and approve the declaration again:

    uv run --python 3.12 migrate.py ~/path/to/project

Your old approvals file, `~/.claude/harness-trusted.json`, is no longer read; delete it when you like.

Run the tests with `uv run pytest`.

---

## Using it

1. **Set up a project once:** in the project, run `/foreman:init`. Review what it proposes, then read the `.foreman.toml` it writes and approve it when the guard asks. Until it is approved, and again after any change to it, the gates are off for that project.
2. **Write a plan** with units and a resume point, and review it yourself. The run builds what the plan says; a bad plan gets built well.
3. **Start a run in a fresh session:** quit and start `claude` again, then

        /foreman:run docs/plans/my-plan.md
        /foreman:run docs/plans/a.md docs/plans/b.md     # a chain: b is built on top of a

4. **Come back to the hand-back.** The last message opens with `Run complete:`, `Blocked on you:` or `Decision needed:` and lists the units done, the decisions made (in the decisions log) and the questions parked.

Do not `/clear` or re-run `/foreman:init` during a run: `/clear` starts a new session the run is not bound to. Merging into protected branches stays your job.

---

## What it does not protect

Read this before you leave a run alone.

- **The guard reads commands like bash, but it is not bash: a speed bump, not a sandbox.** It follows quotes, separators, redirects, heredocs, `$(...)`, nested `bash -c` and `eval` strings, `cd` and variables set in the same command, and resolves every path a known writing command touches (`rm`, `mv`, `cp`, `sed -i`, `perl -i`, `tee`, `git checkout`, ...). It cannot see what a program decides for itself: a Python or Node script that writes a file, `eval` of a variable set elsewhere, git aliases or `push.default` from your config, a branch name read from a file, a `cd` inside a subshell, `tar -C` or `find -delete` with a pattern that happens to match. A writing command with a path it cannot resolve asks when the command names one of Foreman's files. One script of Foreman's own is such a route: `export.py`, used while developing Foreman, rewrites the installed plugin without a prompt. Where the answer matters and it cannot work one out (a push to a branch it cannot read, a branch switched to one it cannot read, a write after a `cd` it cannot follow, a shell fed by another program such as `curl … | sh`, a quote left open, nesting deeper than it reads, a check that takes longer than 7 s), it asks or blocks; commands longer than 200,000 characters always ask. Replayed over the 448 Bash commands of one real nine-unit run, the previous version asked once and this one not at all. Use it to catch mistakes, not an adversary. Run in `auto` or a similar permission mode you already trust.
- **A declaration changed by git holds the run.** `git checkout`, `reset` or `pull` can change `.foreman.toml` without naming it, so the guard does not ask. The changed file no longer matches your approval; during a run the stop gate and the unit gate then hold the session until the approved version is restored or it hands back with `Blocked on you:`, and after 3 attempts they let go with a warning and the gates stay off until you approve it again. Outside a run it is a warning at each stop and at the next session start.
- **Nothing isolates the network.** An unattended session can reach everything your shell can: SSH tunnels, VPNs, cloud credentials, local ports. In one pilot run, a simulator's default port was held by an SSH tunnel to a production server, and test traffic went there before the session noticed. Close tunnels you do not want a run to use, and give local test services ports nothing else uses.
- **Approving `.foreman.toml` means trusting its check command.** The stop gate runs that command after every unit. Read it before you approve.
- **The judge is a small model.** It was tuned on 17 constructed cases and blocks most "shall I continue?" stops, but it can be wrong both ways. Three blocks per step, then it lets the session stop and records a warning.
- **If a hook crashes, the stop gate lets the session stop and the unit gate lets the subagent start** (both fail open) while the guard blocks the call (fails closed). The doctor reports a broken plugin at the next session start. A plugin that is disabled cannot report anything: check `claude plugin list` if the gates seem quiet.

---

## Files

    .claude-plugin/   plugin.json (the plugin) and marketplace.json (this repository as a marketplace)
    hooks/            hooks.json, launch.sh (finds uv), the hooks and their shared modules
    skills/           init and run, invoked as /foreman:init and /foreman:run
    run-contract.md   the rules of a run, printed when a run starts
    uninstall.py      removes the pre-plugin install
    LICENSE           MIT
    tests/
