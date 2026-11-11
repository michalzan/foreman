import subprocess
import textwrap

import pytest

DECL = textwrap.dedent('''\
    [check]
    command = "true"
    timeout_seconds = 30

    [branches]
    working = "dev"
    protected = ["test", "main"]

    [frontend]
    paths = ["frontend/src/"]

    [plan]
    decisions_log = "DECISIONS.md"
    parked = "PARKED.md"

    [audit]
    date = "2026-10-10"
    fingerprint = ""
    files = ["Makefile"]
''')


@pytest.fixture(autouse=True)
def trust_store(tmp_path, monkeypatch):
    import trust
    path = tmp_path / "trusted.json"
    monkeypatch.setattr(trust, "store_path", lambda: path)
    return path


@pytest.fixture(autouse=True)
def launched_with_the_stamp(monkeypatch):
    """Hooks run as hooks.json launches them: with the stamp of the registrations as their first argument."""
    import sys
    import hookio
    monkeypatch.setattr(sys, "argv", ["hook", hookio.stamp()])


def write_decl(root, text):
    """Rewrite the declaration and approve it, as the user would after reviewing a change."""
    import trust
    (root / ".foreman.toml").write_text(text)
    trust.approve(root)


def sh(root, *args):
    return subprocess.run(args, cwd=root, check=True, capture_output=True, text=True).stdout


def raw_commit(repo, subject: bytes) -> None:
    """Commit HEAD's tree on top of HEAD with these exact message bytes (git commit would re-encode them)."""
    tree, parent = sh(repo, "git", "rev-parse", "HEAD^{tree}").strip(), sh(repo, "git", "rev-parse", "HEAD").strip()
    body = b"tree %s\nparent %s\nauthor a <a@a> 1 +0000\ncommitter a <a@a> 1 +0000\n\n%s\n" % (
        tree.encode(), parent.encode(), subject)
    sha = subprocess.run(["git", "hash-object", "-t", "commit", "-w", "--stdin"], cwd=repo, input=body,
                         check=True, capture_output=True).stdout.decode().strip()
    sh(repo, "git", "reset", "-q", "--hard", sha)


def stage_bad_name(repo) -> None:
    """Stage bad\\xff.txt through the index: APFS refuses to create such a file, though a clone can carry the name."""
    blob = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=repo, input=b"x", check=True,
                          capture_output=True).stdout.strip()
    sh(repo, "git", "update-index", "--add", "--cacheinfo", b"100644,%s,bad\xff.txt" % blob)


@pytest.fixture
def repo(tmp_path, trust_store):
    root = tmp_path / "proj"
    root.mkdir()
    sh(root, "git", "init", "-q", "-b", "dev")
    sh(root, "git", "config", "user.email", "t@example.com")
    sh(root, "git", "config", "user.name", "t")
    write_decl(root, DECL)
    (root / ".gitignore").write_text(".foreman/\n")
    (root / "DECISIONS.md").write_text("# Decisions\n")
    (root / "PARKED.md").write_text("# Parked\n")
    (root / "Makefile").write_text("check:\n\ttrue\n")
    sh(root, "git", "add", "-A")
    sh(root, "git", "commit", "-q", "-m", "init")
    return root


def in_run(root):
    """A run owned by another session: the stop gate checks, and the judge (it judges only its own session) stays out."""
    from state import State
    st = State(root)
    st.start_run([("PLAN.md", ["W1"])], sh(root, "git", "rev-parse", "HEAD").strip())
    st.bind_run("other")


@pytest.fixture(autouse=True)
def no_tunnels(request, monkeypatch):
    """Tests must not depend on the tunnels open on this machine."""
    if request.module.__name__ != "test_tunnels":
        import tunnels
        monkeypatch.setattr(tunnels, "warning", lambda run=None: "")


def write_transcript(path, entries):
    """A Claude Code transcript: one JSON object per line."""
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in entries))


@pytest.fixture
def cet(monkeypatch):
    """Local time is UTC+1, as on the machine of the pilot runs; transcripts are in UTC."""
    import time
    monkeypatch.setenv("TZ", "CET-1")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()
