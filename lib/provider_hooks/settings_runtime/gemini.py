from __future__ import annotations

from pathlib import Path

from .common import load_json, save_json, workspace_key


def install_gemini_hooks(
    *,
    home_root: Path,
    command: str | None = None,
    after_agent_command: str | None = None,
    before_agent_command: str | None = None,
) -> Path:
    """注入 Gemini sandbox 的 hooks。

    向后兼容：command= 等价于 after_agent_command=（legacy）。
    新代码请使用 after_agent_command + before_agent_command。
    """
    if command is not None and after_agent_command is None:
        after_agent_command = command

    settings_path = _gemini_settings_path(home_root)
    data = load_json(settings_path) if settings_path.exists() else {}
    hooks = data.get('hooks')
    if not isinstance(hooks, dict):
        hooks = {}
    data['hooks'] = hooks

    if after_agent_command is not None:
        _append_event(hooks, 'AfterAgent', after_agent_command)
    if before_agent_command is not None:
        _append_event(hooks, 'BeforeAgent', before_agent_command)

    return save_json(settings_path, data)


def _append_event(hooks: dict, event_name: str, command: str) -> None:
    """Helper: append event hook entry to hooks dict, idempotent."""
    entries = hooks.get(event_name)
    if not isinstance(entries, list):
        entries = []
    if not gemini_event_has_command(entries, command):
        entries.append({
            'matcher': '*',
            'hooks': [{'type': 'command', 'command': command}],
        })
    hooks[event_name] = entries


def trust_gemini_workspace(*, home_root: Path, workspace_path: Path) -> Path:
    trust_path = _trusted_folders_path(home_root)
    data = load_json(trust_path) if trust_path.exists() else {}
    data[workspace_key(workspace_path)] = 'TRUST_FOLDER'
    save_json(trust_path, data)
    return trust_path


def gemini_event_has_command(groups: list[object], command: str) -> bool:
    for group in groups:
        if not isinstance(group, dict):
            continue
        hooks = group.get('hooks')
        if not isinstance(hooks, list):
            continue
        for hook in hooks:
            if not isinstance(hook, dict):
                continue
            if str(hook.get('type') or '').strip().lower() != 'command':
                continue
            if str(hook.get('command') or '').strip() == command:
                return True
    return False


def _gemini_settings_path(home_root: Path) -> Path:
    return Path(home_root).expanduser() / '.gemini' / 'settings.json'


def _trusted_folders_path(home_root: Path) -> Path:
    return Path(home_root).expanduser() / '.gemini' / 'trustedFolders.json'


__all__ = ['install_gemini_hooks', 'trust_gemini_workspace']
