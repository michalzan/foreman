import os
from pathlib import Path

import gitutil
from conftest import raw_commit, sh, stage_bad_name


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


def test_subjects_newest_first_with_a_limit_and_empty_for_an_unknown_start(repo):
    start = gitutil.head(repo)
    for n in ("a", "b", "c"):
        (repo / f"{n}.txt").write_text(n)
        sh(repo, "git", "add", "-A")
        sh(repo, "git", "commit", "-qm", f"U1: add {n}")
    assert gitutil.subjects(repo, start) == ["U1: add c", "U1: add b", "U1: add a"]
    assert gitutil.subjects(repo, start, limit=2) == ["U1: add c", "U1: add b"]
    assert gitutil.subjects(repo, "deadbeef" * 5) == []
    assert gitutil.subjects(repo, "") == []


def test_a_commit_subject_that_is_not_utf8_still_gives_a_list(repo):
    start = gitutil.head(repo)
    raw_commit(repo, b"U1: bad \xff byte")
    assert gitutil.subjects(repo, start) == ["U1: bad \ufffd byte"]


def test_subjects_cannot_be_given_an_option_for_a_range(repo):
    assert gitutil.subjects(repo, "--output=written") == []
    assert not (repo / "written..HEAD").exists()


def test_a_staged_name_that_is_not_utf8_is_listed_and_keeps_its_bytes(repo):
    stage_bad_name(repo)
    assert [os.fsencode(n) for n in gitutil.dirty_files(repo)] == [b"bad\xff.txt"]


def test_the_fingerprint_sees_the_content_of_a_name_that_is_not_utf8(repo, monkeypatch):
    """The file is faked because APFS refuses the name. The lookup is by exact bytes, so a mangled name reads as gone."""
    stage_bad_name(repo)
    disk = {b"bad\xff.txt": b"1"}
    is_file, read_bytes = Path.is_file, Path.read_bytes
    monkeypatch.setattr(Path, "is_file", lambda p: os.fsencode(p.name) in disk or is_file(p))
    monkeypatch.setattr(Path, "read_bytes", lambda p: disk.get(os.fsencode(p.name)) or read_bytes(p))
    before = gitutil.fingerprint(repo)
    assert gitutil.fingerprint(repo) == before
    disk[b"bad\xff.txt"] = b"2"
    assert gitutil.fingerprint(repo) != before
