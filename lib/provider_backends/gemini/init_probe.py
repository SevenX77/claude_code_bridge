"""Gemini TUI ready detection probe (Q3 Stage 1b).

Implements provider-specific InitGateProbe for Gemini CLI cold-start
detection — checks for banner dissipation and input prompt readiness.

Unlike Codex (which has a per-agent bridge process), Gemini has no bridge —
the probe is instantiated and driven by ccbd's main loop via the
ProjectKeeper._tick_init_gates() pump (see Q3 Stage 1b DESIGN §6).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from provider_core.init_gate import InitGateProbe


# Banner/welcome screen strings that indicate Gemini TUI is NOT yet ready.
# Approximate list from Gemini CLI 0.38.x cold-start; calibrate against
# real cold-start captures when integrating in Step 3 (Q3 Stage 1b DESIGN §5
# explicitly flags this).
GEMINI_INIT_BANNERS: tuple[str, ...] = (
    "Welcome to Gemini",
    "Sign in to Gemini",
    "Choose an authentication method",
    "Trust this folder",
)


# Idle-prompt prefixes Gemini may render (after stripping leading whitespace).
# Two known variants: plain "> " and the diamond-marker "✦ ".
GEMINI_PROMPT_PREFIXES: tuple[str, ...] = ("> ", "✦ ")


class GeminiInitProbe:
    """Probe Gemini TUI for 'input box ready' state.

    Implements InitGateProbe protocol for use with InitGate.

    Ready criteria (AND — all must be true):
        S1: Welcome banner strings NOT present in visible screen
        S2: Last non-empty line starts with one of GEMINI_PROMPT_PREFIXES

    Uses visible-screen-only capture (no scrollback) to avoid false
    negatives from historical banner in scrollback. Stability (S3) is
    layered on top by InitGate via consecutive-true counting; the probe
    itself is stateless.
    """

    def __init__(
        self,
        *,
        pane_id: str,
        tmux_run_fn: Callable[[list[str]], str],
    ) -> None:
        """Initialize probe.

        Args:
            pane_id: Tmux pane identifier (e.g., "%4")
            tmux_run_fn: Callable that runs tmux command and returns stdout.
                         Expected signature: (args: list[str]) -> str
        """
        self._pane_id = pane_id
        self._tmux_run = tmux_run_fn

    def detect(self) -> bool:
        """Return True if Gemini TUI is ready for input.

        Conservative: any error or ambiguity returns False.
        """
        try:
            capture = self._capture_visible()
        except Exception:
            # tmux failure or capture error — conservative fail
            return False

        return self._banner_gone(capture) and self._prompt_on_last_line(capture)

    def _capture_visible(self) -> str:
        """Capture visible screen (no scrollback) from pane.

        Uses `capture-pane -p -t <pane>` without `-S` parameter,
        so only currently visible lines are returned.
        """
        args = ["capture-pane", "-p", "-t", self._pane_id]
        return self._tmux_run(args)

    def _banner_gone(self, capture: str) -> bool:
        """S1: Check that welcome banner strings are NOT present."""
        capture_lower = capture.lower()
        for banner in GEMINI_INIT_BANNERS:
            if banner.lower() in capture_lower:
                return False
        return True

    def _prompt_on_last_line(self, capture: str) -> bool:
        """S2: Check that last non-empty line is an idle input prompt.

        Gemini idle prompt has two known variants — accept either.
        """
        lines = [ln for ln in capture.splitlines() if ln.strip()]
        if not lines:
            return False

        last = lines[-1].lstrip()
        return any(last.startswith(p) for p in GEMINI_PROMPT_PREFIXES)

    def capture_visible_for_diagnostics(self) -> str:
        """Expose visible capture for InitGate diagnostics.

        Returns empty string on any error (conservative).
        """
        try:
            return self._capture_visible()
        except Exception:
            return ""
