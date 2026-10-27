import gitutil
from conftest import sh


def test_fingerprint_stable_then_changes_with_content(repo):
    a = gitutil.fingerprint(repo)
    assert gitutil.fingerprint(repo) == a
    (repo / "x.py").write_text("1")
    b = gitutil.fingerprint(repo)
    assert b != a
    (repo / "x.py").write_text("2")
    assert gitutil.fingerprint(repo) != b


def test_fingerprint_ignores_harness_state(repo):
    a = gitutil.fingerprint(repo)
    (repo / ".foreman").mkdir()
    (repo / ".foreman" / "state.json").write_text("{}")
    assert gitutil.fingerprint(repo) == a


def test_names_with_spaces_and_accents_survive(repo):
    (repo / "docs").mkdir()
    (repo / "docs" / "aktivita - Callcenter.docx").write_text("x")
    (repo / "Požičovňa.md").write_text("x")
    assert gitutil.dirty_files(repo) == ["Požičovňa.md", "docs/aktivita - Callcenter.docx"]


def test_changed_files_covers_commits_and_working_tree(repo):
    start = gitutil.head(repo)
    (repo / "a.py").write_text("a")
    sh(repo, "git", "add", "-A")
    sh(repo, "git", "commit", "-q", "-m", "a")
    (repo / "b.py").write_text("b")
    assert gitutil.changed_files(repo, start) == ["a.py", "b.py"]
    assert gitutil.changed_files(repo, "") == ["b.py"]
    assert gitutil.changed_files(repo, "0" * 40) == ["b.py"]  # unknown commit: no crash


def test_empty_repo_and_detached_head(tmp_path, repo):
    empty = tmp_path / "empty"
    empty.mkdir()
    sh(empty, "git", "init", "-q")
    assert gitutil.head(empty) == ""
    assert gitutil.current_branch(empty) == ""
    assert gitutil.fingerprint(empty)
    sh(repo, "git", "checkout", "-q", "--detach")
    assert gitutil.current_branch(repo) == ""
