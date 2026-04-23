"""Agent activity detection from pane tail.

Used to confirm a Gemini/Codex/Claude CLI started processing a prompt
before the BeforeAgent reception artifact appears (handles hook fail-open).

This module provides a marker-based detection system for identifying when
an agent (Claude/Codex/Gemini) is actively processing. It is part of the
D3 double-insurance failsafe to prevent duplicate reception hooks.
"""
from __future__ import annotations


# 常量集——根据观察到的 CLI 输出维护。新版 CLI 升级时 patch 这里。
AGENT_ACTIVITY_MARKERS: tuple[str, ...] = (
    'Planning',
    'Thinking',
    'Generating',
    'Calling',
    'Loading',
    '✦',                          # Gemini CLI 决定/响应前缀
    '⠋', '⠙', '⠹', '⠸',           # Braille spinner frames (常见)
    '⠼', '⠴', '⠦', '⠧', '⠇', '⠏',
)


def pane_shows_agent_activity(pane_tail: str) -> bool:
    """Return True if any activity marker appears in the pane tail string."""
    if not pane_tail:
        return False
    return any(marker in pane_tail for marker in AGENT_ACTIVITY_MARKERS)


__all__ = ['AGENT_ACTIVITY_MARKERS', 'pane_shows_agent_activity']
