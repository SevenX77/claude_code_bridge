"""Unit tests for ClaudeInitProbe (Q3 Stage 1c).

Mirrors test_gemini_init_probe.py structure and naming.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from provider_backends.claude.init_probe import (
    CLAUDE_INIT_BANNERS,
    CLAUDE_PROMPT_PREFIXES,
    CLAUDE_STEADY_MARKERS,
    ClaudeInitProbe,
)


def _make_probe(tmux_capture_output: str = "", *, raise_on_call: bool = False):
    fn = MagicMock()
    if raise_on_call:
        fn.side_effect = RuntimeError("tmux dead")
    else:
        fn.return_value = tmux_capture_output
    return ClaudeInitProbe(pane_id="%2", tmux_run_fn=fn), fn


# Realistic steady-state capture sourced from a live Claude TUI.
READY_CAPTURE = """\
▝▜█████▛▘  Sonnet 4.6 · API Usage Billing
  ▘▘ ▝▝    /home/sevenx
────────────────────────────────────────────────────────────────────────────────
❯
────────────────────────────────────────────────────────────────────────────────
  /home/sevenx | Sonnet 4.6 | ctx 0% | $0 | 5h 0% | wk 0%
"""

WELCOME_CAPTURE = """\
Welcome to Claude Code

Setup Wizard
Choose your preferred theme
"""

PARTIAL_CAPTURE_NO_MARKER = """\
────────────────────────────────────────────────────────────────────────────────
❯
────────────────────────────────────────────────────────────────────────────────
"""

EMPTY_PROMPT_CAPTURE = """\
Sonnet 4.6
some random text
no prompt here
"""


class TestBannerDetection:
    def test_welcome_banner_present_returns_false(self):
        probe, _ = _make_probe(WELCOME_CAPTURE)
        assert probe.detect() is False

    def test_ready_capture_returns_true(self):
        probe, _ = _make_probe(READY_CAPTURE)
        assert probe.detect() is True

    def test_setup_wizard_alone_returns_false(self):
        probe, _ = _make_probe("Setup Wizard\n\nSonnet 4.6\n❯ ")
        assert probe.detect() is False

    def test_trust_dialog_returns_false(self):
        probe, _ = _make_probe("Trust the files in this folder\n❯ \nSonnet")
        assert probe.detect() is False


class TestPromptPresence:
    def test_chevron_in_box_passes(self):
        probe, _ = _make_probe(READY_CAPTURE)
        assert probe.detect() is True

    def test_no_chevron_returns_false(self):
        probe, _ = _make_probe(EMPTY_PROMPT_CAPTURE)
        assert probe.detect() is False

    def test_chevron_indented_with_spaces_still_recognized(self):
        capture = "Sonnet 4.6\n   ❯ \n"
        probe, _ = _make_probe(capture)
        assert probe.detect() is True


class TestSteadyMarker:
    def test_no_model_marker_returns_false(self):
        capture = "────────\n❯ \n────────\nproject_dir | ctx 0%\n"
        probe, _ = _make_probe(capture)
        # banner_gone=True, prompt_present=True, steady_marker=False
        assert probe.detect() is False

    def test_haiku_marker_passes(self):
        capture = "Haiku 4.5\n❯ \n"
        probe, _ = _make_probe(capture)
        assert probe.detect() is True

    def test_opus_marker_passes(self):
        capture = "Opus 4.7\n❯ \n"
        probe, _ = _make_probe(capture)
        assert probe.detect() is True


class TestCaptureBehavior:
    def test_capture_uses_visible_only(self):
        probe, fn = _make_probe(READY_CAPTURE)
        probe.detect()
        called_args = fn.call_args[0][0]
        # Must NOT include -S (scrollback flag)
        assert "-S" not in called_args
        assert "capture-pane" in called_args
        assert "-p" in called_args
        assert "%2" in called_args


class TestEdgeCases:
    def test_empty_capture_returns_false(self):
        probe, _ = _make_probe("")
        assert probe.detect() is False

    def test_tmux_run_exception_returns_false(self):
        probe, _ = _make_probe(raise_on_call=True)
        assert probe.detect() is False

    def test_diagnostics_returns_capture_on_success(self):
        probe, _ = _make_probe(READY_CAPTURE)
        assert probe.capture_visible_for_diagnostics() == READY_CAPTURE

    def test_diagnostics_returns_empty_on_failure(self):
        probe, _ = _make_probe(raise_on_call=True)
        assert probe.capture_visible_for_diagnostics() == ""

    def test_constants_exposed_for_tooling(self):
        # Smoke test: callers (e.g. integration tests) can import.
        assert "Welcome to Claude Code" in CLAUDE_INIT_BANNERS
        assert "❯ " in CLAUDE_PROMPT_PREFIXES
        assert "Sonnet" in CLAUDE_STEADY_MARKERS

    def test_steady_marker_match_is_case_sensitive(self):
        # Documents: marker matching is intentionally case-sensitive
        # because Claude Code always uses TitleCase model names in the
        # status bar. Lowercase 'sonnet' in arbitrary user text must NOT
        # falsely qualify the pane as ready.
        capture = "sonnet 4.6\n❯ \nbanner-gone\n"
        probe, _ = _make_probe(capture)
        assert probe.detect() is False
