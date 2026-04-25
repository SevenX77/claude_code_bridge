"""Unit tests for GeminiInitProbe (Q3 Stage 1b Step 2)."""
from __future__ import annotations

import pytest

from provider_backends.gemini.init_probe import (
    GEMINI_INIT_BANNERS,
    GEMINI_PROMPT_PREFIXES,
    GeminiInitProbe,
)


class TestBannerDetection:
    """S1: welcome banner detection."""

    def test_banner_present_returns_false(self):
        """Capture with "Welcome to Gemini" → detect() returns False."""
        def tmux_run(args: list[str]) -> str:
            return "Welcome to Gemini\nSign in to Gemini\n"

        probe = GeminiInitProbe(pane_id="%4", tmux_run_fn=tmux_run)
        assert probe.detect() is False

    def test_banner_gone_prompt_ready_returns_true(self):
        """Capture with idle ✦ prompt on last line → detect() True."""
        def tmux_run(args: list[str]) -> str:
            return "\n✦ Type a message\n"

        probe = GeminiInitProbe(pane_id="%4", tmux_run_fn=tmux_run)
        assert probe.detect() is True

    def test_plain_arrow_prompt_returns_true(self):
        """Banner gone + last line "> " (plain arrow variant) → True."""
        def tmux_run(args: list[str]) -> str:
            return "Some history\n> Hello\n"

        probe = GeminiInitProbe(pane_id="%4", tmux_run_fn=tmux_run)
        assert probe.detect() is True


class TestPromptPosition:
    """S2: prompt-on-last-line."""

    def test_prompt_not_on_last_line_returns_false(self):
        """Idle prompt buried mid-output → detect() False."""
        def tmux_run(args: list[str]) -> str:
            return "✦ Hello\nChoose an authentication method\n[Enter] continue\n"

        probe = GeminiInitProbe(pane_id="%4", tmux_run_fn=tmux_run)
        assert probe.detect() is False

    def test_no_recognized_prefix_returns_false(self):
        """Last line not starting with any GEMINI_PROMPT_PREFIXES → False."""
        def tmux_run(args: list[str]) -> str:
            return "Some chatter\nfinal line without arrow\n"

        probe = GeminiInitProbe(pane_id="%4", tmux_run_fn=tmux_run)
        assert probe.detect() is False


class TestBannerVariants:
    """All GEMINI_INIT_BANNERS strings cause detect() to return False."""

    def test_all_banner_variants_detected(self):
        for banner in GEMINI_INIT_BANNERS:

            def make_tmux_run(b: str):
                def tmux_run(args: list[str]) -> str:
                    return f"Some line\n{b}\n✦ prompt\n"
                return tmux_run

            probe = GeminiInitProbe(pane_id="%4", tmux_run_fn=make_tmux_run(banner))
            result = probe.detect()
            assert result is False, f"Banner '{banner}' should cause False, got {result}"


class TestCaptureBehavior:
    """capture-pane invocation shape."""

    def test_capture_uses_visible_only(self):
        """tmux args = ['capture-pane', '-p', '-t', pane_id]; no -S flag."""
        captured_args: list[str] = []

        def tmux_run(args: list[str]) -> str:
            captured_args.extend(args)
            return "✦ Ready\n"

        probe = GeminiInitProbe(pane_id="%7", tmux_run_fn=tmux_run)
        probe.detect()

        assert captured_args == ["capture-pane", "-p", "-t", "%7"]
        assert "-S" not in captured_args


class TestEdgeCases:
    """Conservative behavior on errors / ambiguous input."""

    def test_empty_capture_returns_false(self):
        def tmux_run(args: list[str]) -> str:
            return ""

        probe = GeminiInitProbe(pane_id="%4", tmux_run_fn=tmux_run)
        assert probe.detect() is False

    def test_tmux_run_exception_returns_false(self):
        def tmux_run(args: list[str]) -> str:
            raise RuntimeError("tmux failed")

        probe = GeminiInitProbe(pane_id="%4", tmux_run_fn=tmux_run)
        assert probe.detect() is False

    def test_diagnostics_returns_empty_on_failure(self):
        def tmux_run(args: list[str]) -> str:
            raise RuntimeError("tmux failed")

        probe = GeminiInitProbe(pane_id="%4", tmux_run_fn=tmux_run)
        assert probe.capture_visible_for_diagnostics() == ""

    def test_diagnostics_returns_capture_on_success(self):
        def tmux_run(args: list[str]) -> str:
            return "✦ ready\n"

        probe = GeminiInitProbe(pane_id="%4", tmux_run_fn=tmux_run)
        assert probe.capture_visible_for_diagnostics() == "✦ ready\n"


class TestPromptIndentation:
    """Leading whitespace on prompt line should not block detection."""

    def test_leading_whitespace_tolerated(self):
        """`  > prompt` (indented) still counts as prompt-ready."""
        def tmux_run(args: list[str]) -> str:
            return "Some chatter\n  > indented\n"

        probe = GeminiInitProbe(pane_id="%4", tmux_run_fn=tmux_run)
        assert probe.detect() is True
