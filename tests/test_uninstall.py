import json

import pytest

import uninstall

FOREIGN = {"type": "command", "command": "node ponytail-activate.js"}
OURS = {"type": "command", "command": '"/opt/homebrew/bin/uv" run --no-project --python 3.12 "$HOME/.claude/hooks/harness/stop_gate.py"'}


def legacy_home(tmp_path):
    """~/.claude as install.py left it before the harness became a plugin."""
    home = tmp_path / "home"
    claude = home / ".claude"
    (claude / "hooks" / "harness").mkdir(parents=True)
    (claude / "hooks" / "harness" / "guard.py").write_text("x")
    for skill in ("harness-init", "harness-run", "mine"):
        (claude / "skills" / skill).mkdir(parents=True)
    (claude / "settings.json").write_text(json.dumps({
        "statusLine": {"type": "command", "command": "bash ~/.claude/statusline.sh"},
        "hooks": {"SessionStart": [{"hooks": [FOREIGN]}], "Stop": [{"hooks": [OURS]}]},
    }))
    (claude / "CLAUDE.md").write_text("# Global Instructions\n\nMine.\n\n" + uninstall.MARK_START
                                      + "\n## Run contract\n- x\n" + uninstall.MARK_END + "\n")
    (claude / "harness-trusted.json").write_text("{}")
    return home


def test_uninstall_removes_the_old_install_and_keeps_everything_else(tmp_path):
    home = legacy_home(tmp_path)
    claude = home / ".claude"
    uninstall.uninstall(home)
    assert not (claude / "hooks" / "harness").exists()
    assert not (claude / "skills" / "harness-run").exists() and not (claude / "skills" / "harness-init").exists()
    assert (claude / "skills" / "mine").is_dir()
    settings = json.loads((claude / "settings.json").read_text())
    assert settings["statusLine"]["command"] == "bash ~/.claude/statusline.sh"
    assert settings["hooks"] == {"SessionStart": [{"hooks": [FOREIGN]}]}
    assert (claude / "CLAUDE.md").read_text() == "# Global Instructions\n\nMine.\n"
    assert (claude / "harness-trusted.json").is_file()  # the user's approvals are theirs to delete
    assert len(list(claude.glob("settings.json.harness-backup-*"))) == 1
    uninstall.uninstall(home)  # a second run is harmless


def test_unparseable_settings_abort_untouched(tmp_path):
    home = legacy_home(tmp_path)
    broken = '{"statusLine": {"type": "command",},}\n'
    (home / ".claude" / "settings.json").write_text(broken)
    with pytest.raises(SystemExit):
        uninstall.uninstall(home)
    assert (home / ".claude" / "settings.json").read_text() == broken
    assert (home / ".claude" / "hooks" / "harness").is_dir()
