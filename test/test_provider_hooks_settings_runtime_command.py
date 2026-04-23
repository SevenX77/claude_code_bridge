from __future__ import annotations

from pathlib import Path

from provider_hooks.settings_runtime.command import build_hook_command


def test_build_hook_command_no_timeout_default() -> None:
    cmd = build_hook_command(
        provider='gemini',
        script_path=Path('/x/hook'),
        python_executable='/p/python3',
        completion_dir=Path('/c'),
        agent_name='a2',
        workspace_path=Path('/w'),
    )
    assert not cmd.startswith('/usr/bin/timeout')
    assert "'/p/python3'" in cmd or "/p/python3" in cmd


def test_build_hook_command_with_timeout_prepends_wrapper() -> None:
    cmd = build_hook_command(
        provider='gemini',
        script_path=Path('/x/hook'),
        python_executable='/p/python3',
        completion_dir=Path('/c'),
        agent_name='a2',
        workspace_path=Path('/w'),
        timeout_s=5,
    )
    assert cmd.startswith('/usr/bin/timeout 5')


def test_build_hook_command_with_event_start_includes_reception() -> None:
    cmd = build_hook_command(
        provider='gemini',
        script_path=Path('/x/hook'),
        python_executable='/p/python3',
        completion_dir=Path('/c'),
        agent_name='a2',
        workspace_path=Path('/w'),
        event='start',
        reception_dir=Path('/r'),
    )
    assert "--event 'start'" in cmd or "--event start" in cmd
    assert "--reception-dir" in cmd
