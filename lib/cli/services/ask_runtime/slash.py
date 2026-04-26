from __future__ import annotations

from terminal_runtime.tmux_send import _is_slash_command


def is_slash_ask_command(command) -> bool:
    return _is_slash_command(str(getattr(command, 'message', '') or ''))


__all__ = ['is_slash_ask_command']
