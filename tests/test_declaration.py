from conftest import write_decl
import pytest

import declaration


def test_find_root_walks_up(repo):
    sub = repo / "a" / "b"
    sub.mkdir(parents=True)
    assert declaration.find_root(sub) == repo.resolve()


def test_find_root_none_outside_a_project(tmp_path):
    assert declaration.find_root(tmp_path) is None


def test_load_reads_every_field(repo):
    d = declaration.load(repo)
    assert d.check_command == "true"
    assert d.check_timeout == 30
    assert d.protected == ("test", "main")
    assert d.frontend_paths == ("frontend/src/",)
    assert d.decisions_log == "DECISIONS.md"
    assert d.parked == "PARKED.md"
    assert d.audit_files == ("Makefile",)


def test_working_branch_is_neither_needed_nor_refused(repo):
    """Each plan brings its own branch; an old declaration that still names one keeps loading unchanged."""
    write_decl(repo, '[check]\ncommand="true"\n[branches]\nprotected=["main"]\n[plan]\ndecisions_log="a"\nparked="b"\n')
    assert declaration.load(repo).protected == ("main",)
    write_decl(repo, '[check]\ncommand="true"\n[branches]\nworking="dev"\nprotected=["main"]\n[plan]\ndecisions_log="a"\nparked="b"\n')
    assert declaration.load(repo).protected == ("main",)


@pytest.mark.parametrize("bad", [
    "not = [valid toml",
    '[check]\ncommand = ""\n[branches]\nworking="dev"\n[plan]\ndecisions_log="a"\nparked="b"\n',
    '[branches]\nworking="dev"\n[plan]\ndecisions_log="a"\nparked="b"\n',
])
def test_load_rejects_broken_declarations(repo, bad):
    write_decl(repo, bad)
    with pytest.raises(declaration.DeclarationError):
        declaration.load(repo)
