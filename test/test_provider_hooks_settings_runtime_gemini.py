from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.provider_hooks.settings_runtime.gemini import install_gemini_hooks


def test_install_injects_both_before_and_after_idempotent(tmp_path: Path) -> None:
    home = tmp_path / "home"
    install_gemini_hooks(
        home_root=home,
        after_agent_command="/usr/bin/timeout 5 /p finish",
        before_agent_command="/usr/bin/timeout 5 /p start",
    )
    settings = json.loads((home / ".gemini" / "settings.json").read_text())
    assert "AfterAgent" in settings["hooks"]
    assert "BeforeAgent" in settings["hooks"]
    assert settings["hooks"]["AfterAgent"][0]["hooks"][0]["command"].startswith("/usr/bin/timeout")
    assert settings["hooks"]["BeforeAgent"][0]["hooks"][0]["command"].startswith("/usr/bin/timeout")

    # idempotent: second call should not duplicate
    install_gemini_hooks(
        home_root=home,
        after_agent_command="/usr/bin/timeout 5 /p finish",
        before_agent_command="/usr/bin/timeout 5 /p start",
    )
    settings2 = json.loads((home / ".gemini" / "settings.json").read_text())
    assert len(settings2["hooks"]["AfterAgent"]) == 1
    assert len(settings2["hooks"]["BeforeAgent"]) == 1


def test_install_legacy_single_command_still_supported(tmp_path: Path) -> None:
    """Legacy command= parameter should still work (backward compatibility)."""
    home = tmp_path / "home"
    install_gemini_hooks(home_root=home, command="/old finish")
    settings = json.loads((home / ".gemini" / "settings.json").read_text())
    assert "AfterAgent" in settings["hooks"]
    assert settings["hooks"]["AfterAgent"][0]["hooks"][0]["command"] == "/old finish"
    assert "BeforeAgent" not in settings.get("hooks", {})
