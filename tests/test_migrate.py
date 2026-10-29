"""migrate.py: move a project from the harness file names to Foreman's (2026-10-27)."""
import pytest

import migrate
from conftest import DECL, sh


@pytest.fixture
def old(tmp_path):
    """A project as the harness left it: .harness.toml tracked, .harness/ state ignored."""
    root = tmp_path / "proj"
    root.mkdir()
    sh(root, "git", "init", "-q", "-b", "dev")
    sh(root, "git", "config", "user.email", "t@example.com")
    sh(root, "git", "config", "user.name", "t")
    (root / ".harness.toml").write_text(DECL)
    (root / ".gitignore").write_text("node_modules/\n.harness/\n")
    sh(root, "git", "add", "-A")
    sh(root, "git", "commit", "-q", "-m", "init")
    (root / ".harness" / "walkthrough").mkdir(parents=True)
    (root / ".harness" / "gate.log").write_text("{}\n")
    return root


def test_moves_declaration_state_and_ignore_line(old):
    out = migrate.migrate(old)
    assert (old / ".foreman.toml").read_text() == DECL and not (old / ".harness.toml").exists()
    assert (old / ".foreman" / "gate.log").is_file() and not (old / ".harness").exists()
    assert (old / ".gitignore").read_text() == "node_modules/\n.foreman/\n"
    assert "R  .harness.toml -> .foreman.toml" in sh(old, "git", "status", "--short")
    assert "trust.py" in out and "approve" in out


def test_an_ignored_declaration_stays_ignored_under_its_new_name(tmp_path):
    """A project may keep its declaration out of git: the new name must not show up as a file to commit."""
    root = tmp_path / "p"
    root.mkdir()
    sh(root, "git", "init", "-q", "-b", "dev")
    (root / ".gitignore").write_text(".harness/\n.harness.toml\n")
    (root / ".harness.toml").write_text(DECL)
    out = migrate.migrate(root)
    assert (root / ".gitignore").read_text() == ".foreman/\n.foreman.toml\n"
    assert sh(root, "git", "status", "--short").strip() == "?? .gitignore"
    assert "Commit the rename" not in out and "Commit .gitignore" in out


def test_works_from_a_subdirectory(old):
    (old / "src").mkdir()
    migrate.migrate(old / "src")
    assert (old / ".foreman.toml").is_file()


def test_refuses_during_a_run(old):
    (old / ".harness" / "run.json").write_text("{}")
    with pytest.raises(SystemExit, match="run is active"):
        migrate.migrate(old)
    assert (old / ".harness.toml").is_file()


def test_a_second_run_changes_nothing(old):
    migrate.migrate(old)
    assert "nothing to migrate" in migrate.migrate(old)


def test_refuses_when_both_names_exist(old):
    (old / ".foreman.toml").write_text(DECL)
    with pytest.raises(SystemExit, match="both"):
        migrate.migrate(old)


def test_an_untracked_declaration_is_moved_too(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    sh(root, "git", "init", "-q")
    (root / ".harness.toml").write_text(DECL)
    migrate.migrate(root)
    assert (root / ".foreman.toml").is_file()
    assert (root / ".gitignore").read_text() == ".foreman/\n"


def test_a_symlinked_gitignore_is_not_written_through(old, tmp_path):
    """Security review, 2026-10-27: the write must not land wherever the link points."""
    target = tmp_path / "elsewhere.txt"
    target.write_text("keep me\n")
    (old / ".gitignore").unlink()
    (old / ".gitignore").symlink_to(target)
    with pytest.raises(SystemExit, match="symlink"):
        migrate.migrate(old)
    assert target.read_text() == "keep me\n"
    assert (old / ".harness.toml").is_file()  # refused before anything moved
