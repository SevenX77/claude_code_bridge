from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from cli.services.provider_hooks import prepare_workspace_provider_hooks
from provider_hooks.settings_runtime.install import install_workspace_completion_hooks


def test_gemini_install_with_before_and_after_creates_both_hooks(tmp_path: Path) -> None:
    home = tmp_path / "home"
    install_workspace_completion_hooks(
        provider="gemini",
        workspace_path=tmp_path / "ws",
        home_root=home,
        after_command="/usr/bin/timeout 5 /py finish-cmd",
        before_command="/usr/bin/timeout 5 /py start-cmd",
    )
    settings = json.loads((home / ".gemini" / "settings.json").read_text())
    assert "AfterAgent" in settings["hooks"]
    assert "BeforeAgent" in settings["hooks"]
    assert settings["hooks"]["AfterAgent"][0]["hooks"][0]["command"].startswith("/usr/bin/timeout")
    assert settings["hooks"]["BeforeAgent"][0]["hooks"][0]["command"].startswith("/usr/bin/timeout")


def test_gemini_install_legacy_command_kw_still_works(tmp_path: Path) -> None:
    """旧调用方传 command= 等价 after_command=，BeforeAgent 不注入。"""
    home = tmp_path / "home"
    install_workspace_completion_hooks(
        provider="gemini",
        workspace_path=tmp_path / "ws",
        home_root=home,
        command="/old finish-cmd",
    )
    settings = json.loads((home / ".gemini" / "settings.json").read_text())
    assert "AfterAgent" in settings["hooks"]
    assert "BeforeAgent" not in settings.get("hooks", {})


def test_claude_install_ignores_before_command(tmp_path: Path) -> None:
    """Claude provider 没 BeforeAgent 概念，传 before_command 应被忽略不报错。"""
    home = tmp_path / "home"
    install_workspace_completion_hooks(
        provider="claude",
        workspace_path=tmp_path / "ws",
        home_root=home,
        after_command="/finish",
        before_command="/start-ignored",
    )
    settings = json.loads((home / ".claude" / "settings.json").read_text())
    assert "Stop" in settings.get("hooks", {}) or "PostToolUse" in settings.get("hooks", {})


def test_prepare_gemini_hooks_creates_reception_dir_and_passes_both_commands(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    home = tmp_path / "home"
    completion_dir = tmp_path / "agent" / "provider-runtime" / "gemini" / "completion"
    expected_reception = tmp_path / "agent" / "provider-runtime" / "gemini" / "reception"

    captured = {}

    def fake_install(*, provider, workspace_path, home_root, **kwargs):
        captured.update({'provider': provider, **kwargs})
        return home_root

    with patch('cli.services.provider_hooks.install_workspace_completion_hooks', side_effect=fake_install):
        prepare_workspace_provider_hooks(
            provider="gemini",
            workspace_path=workspace,
            completion_dir=completion_dir,
            agent_name="a2",
            home_root=home,
        )

    assert expected_reception.exists(), "reception dir must be mkdir-ed"
    assert "after_command" in captured and "/usr/bin/timeout 5" in captured["after_command"]
    assert "before_command" in captured and "/usr/bin/timeout 5" in captured["before_command"]
    assert "--event 'start'" in captured["before_command"] or "--event start" in captured["before_command"]
    assert "--reception-dir" in captured["before_command"]
