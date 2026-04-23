from __future__ import annotations
import json
from pathlib import Path
from provider_hooks.settings_runtime.gemini import install_gemini_hooks

def test_install_replaces_legacy_hook_not_duplicates(tmp_path: Path) -> None:
    """安装新式 hook 时应清理旧式 entry（command 含 ccb-provider-finish-hook basename），
    最终只剩 1 个 entry 且是新 command。
    """
    home = tmp_path / "home"
    
    # 先写入老式的 settings.json（模拟生产已有状态）
    old_cmd = "/home/sevenx/coding/claude_code_bridge/bin/ccb-provider-finish-hook --provider gemini --completion-dir /old"
    settings_path = home / ".gemini" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({
        "hooks": {
            "AfterAgent": [
                {"matcher": "*", "hooks": [{"type": "command", "command": old_cmd}]}
            ]
        }
    }))
    
    # 现在安装新式的（带 timeout 包装）
    new_cmd = "/usr/bin/timeout 5 /home/sevenx/coding/claude_code_bridge/bin/ccb-provider-finish-hook --provider gemini --event finish --completion-dir /new"
    install_gemini_hooks(home_root=home, after_agent_command=new_cmd)
    
    # 验证：AfterAgent 只剩 1 个 entry，且是新 command
    settings = json.loads(settings_path.read_text())
    after_entries = settings.get("hooks", {}).get("AfterAgent", [])
    assert len(after_entries) == 1, f"应只剩 1 个 entry，实际: {after_entries}"
    hooks = after_entries[0].get("hooks", [])
    assert len(hooks) == 1
    assert hooks[0]["command"] == new_cmd

def test_install_preserves_foreign_hooks(tmp_path: Path) -> None:
    """清理时只删含 ccb-provider-finish-hook 的 entry，外部 hook 要保留。"""
    home = tmp_path / "home"
    
    foreign_cmd = "/usr/local/bin/my-custom-hook.sh"
    old_cmd = "/old/bin/ccb-provider-finish-hook --provider gemini"
    
    settings_path = home / ".gemini" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({
        "hooks": {
            "AfterAgent": [
                {"matcher": "*", "hooks": [{"type": "command", "command": foreign_cmd}]},
                {"matcher": "*", "hooks": [{"type": "command", "command": old_cmd}]}
            ]
        }
    }))
    
    new_cmd = "/usr/bin/timeout 5 /new/bin/ccb-provider-finish-hook --provider gemini"
    install_gemini_hooks(home_root=home, after_agent_command=new_cmd)
    
    settings = json.loads(settings_path.read_text())
    after_entries = settings.get("hooks", {}).get("AfterAgent", [])
    commands = []
    for entry in after_entries:
        for hook in entry.get("hooks", []):
            commands.append(hook.get("command"))
    
    assert foreign_cmd in commands, "外部 hook 应保留"
    assert old_cmd not in commands, "旧式 hook 应被清理"
    assert new_cmd in commands, "新式 hook 应存在"
    assert len(commands) == 2, f"应只剩 2 个 command，实际: {commands}"

def test_install_idempotent_repeated_calls(tmp_path: Path) -> None:
    """多次调用同一 command 不应重复追加。"""
    home = tmp_path / "home"
    cmd = "/usr/bin/timeout 5 /bin/ccb-provider-finish-hook --provider gemini"
    
    install_gemini_hooks(home_root=home, after_agent_command=cmd)
    install_gemini_hooks(home_root=home, after_agent_command=cmd)
    install_gemini_hooks(home_root=home, after_agent_command=cmd)
    
    settings_path = home / ".gemini" / "settings.json"
    settings = json.loads(settings_path.read_text())
    after_entries = settings.get("hooks", {}).get("AfterAgent", [])
    
    commands = []
    for entry in after_entries:
        for hook in entry.get("hooks", []):
            commands.append(hook.get("command"))
    
    assert commands.count(cmd) == 1, f"应只出现 1 次，实际: {commands}"

def test_install_both_before_and_after(tmp_path: Path) -> None:
    """同时安装 BeforeAgent 和 AfterAgent。"""
    home = tmp_path / "home"
    before_cmd = "/usr/bin/timeout 5 /bin/ccb-provider-finish-hook --event start"
    after_cmd = "/usr/bin/timeout 5 /bin/ccb-provider-finish-hook --event finish"
    
    install_gemini_hooks(home_root=home, before_agent_command=before_cmd, after_agent_command=after_cmd)
    
    settings_path = home / ".gemini" / "settings.json"
    settings = json.loads(settings_path.read_text())
    
    assert "BeforeAgent" in settings.get("hooks", {})
    assert "AfterAgent" in settings.get("hooks", {})
