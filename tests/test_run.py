import gitutil
import run
from state import State


def test_start_status_stop(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    (repo / "PLAN.md").write_text("# plan\n")
    assert run.main(["start", "--plan", "PLAN.md", "W8", "W9"]) == 0
    r = State(repo).run()
    assert State.plan_files(r) == ["PLAN.md"] and r["units"] == ["W8", "W9"]
    assert r["start_head"] == gitutil.head(repo)
    assert run.main(["status"]) == 0
    assert "W8, W9" in capsys.readouterr().out
    assert run.main(["stop"]) == 0
    assert State(repo).run() is None


def test_plain_run_is_recorded(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    (repo / "PLAN.md").write_text("# plan\n")
    assert run.main(["start", "--plain", "--plan", "PLAN.md", "W8"]) == 0
    assert State(repo).run()["plain"] is True
    run.main(["status"])
    assert "plain" in capsys.readouterr().out


def test_start_refuses_a_missing_plan_file(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    assert run.main(["start", "--plan", "NOPE.md", "W8"]) == 1
    assert State(repo).run() is None


def test_refuses_outside_a_declared_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert run.main(["status"]) == 1


def test_a_chain_of_plans(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    (repo / "A.md").write_text("a")
    (repo / "B.md").write_text("b")
    assert run.main(["start", "--plan", "A.md", "a1", "a2", "--plan", "B.md", "b1"]) == 0
    assert State.plan_files(State(repo).run()) == ["A.md", "B.md"]
    run.main(["status"])
    out = capsys.readouterr().out
    assert "a1, a2 (A.md)" in out and "then b1 (B.md)" in out


def test_a_chain_refuses_a_plan_without_units(repo, monkeypatch):
    monkeypatch.chdir(repo)
    (repo / "A.md").write_text("a")
    (repo / "B.md").write_text("b")
    assert run.main(["start", "--plan", "A.md", "a1", "--plan", "B.md"]) == 1
    assert State(repo).run() is None


def test_start_names_the_ports_tunnels_hold(repo, monkeypatch, capsys):
    import tunnels
    monkeypatch.setattr(tunnels, "warning", lambda run=None: "local ports held by tunnels: 8090 (ssh x)")
    monkeypatch.chdir(repo)
    (repo / "PLAN.md").write_text("# plan\n")
    assert run.main(["start", "--plan", "PLAN.md", "W8"]) == 0
    assert "8090 (ssh x)" in capsys.readouterr().out


def test_start_prints_the_run_contract(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    (repo / "PLAN.md").write_text("# plan\n")
    assert run.main(["start", "--plan", "PLAN.md", "W8"]) == 0
    out = capsys.readouterr().out
    assert "## Run contract" in out and "Follow the plan's order" in out
