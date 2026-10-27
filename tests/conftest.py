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


def write_decl(root, text):
    """Rewrite the declaration and approve it, as the user would after reviewing a change."""
    import trust
    (root / ".foreman.toml").write_text(text)
    trust.approve(root)


def sh(root, *args):
    return subprocess.run(args, cwd=root, check=True, capture_output=True, text=True).stdout


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


@pytest.fixture(autouse=True)
def no_tunnels(request, monkeypatch):
    """Tests must not depend on the tunnels open on this machine."""
    if request.module.__name__ != "test_tunnels":
        import tunnels
        monkeypatch.setattr(tunnels, "warning", lambda run=None: "")
