"""Claude Code TUI ready detection probe (Q3 Stage 1c).

Implements provider-specific InitGateProbe for Claude Code CLI cold-start
detection — checks for setup-wizard / welcome banner dissipation and the
input prompt being rendered.

Mirrors ``provider_backends.gemini.init_probe.GeminiInitProbe`` structure
exactly. ccbd's ``InitGateDriver`` (Q3 Stage 1b Step 3) drives this probe
once per heartbeat; registration happens in
``ccbd.services.init_gate_registration`` when a Claude agent is mounted.

Banner / prompt strings are calibrated against Claude Code 2026-04 TUI.
If Anthropic ships a UI revision, update the constants here AND re-run
the probe tests to confirm. Probe-failure semantics (per Step 4 design)
are fall-through with WARNING — the reception-driven retry remains the
actual delivery guarantee, so a stale banner list degrades gracefully
rather than hard-blocking sends.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from provider_core.init_gate import InitGateProbe


# Banner / setup-wizard strings that indicate Claude Code TUI is NOT yet
# ready. Drawn from Claude Code 2026-04 cold-start screens (welcome
# splash, theme picker, login prompt, setup wizard, trust dialog). When
# any of these is present in the visible pane, the probe returns False.
CLAUDE_INIT_BANNERS: tuple[str, ...] = (
    "Welcome to Claude Code",
    "Setup Wizard",
    "Choose the text style",
    "Choose your preferred theme",
    "Let's get started",
    "Trust the files in this folder",
    "Press Enter to continue",
)


# Idle-prompt prefix Claude Code renders inside its input box.
# Calibrated against the captured pane:
#   ────────...
#   ❯
#   ────────...
# Both the `❯` glyph and the trailing space are common variants.
CLAUDE_PROMPT_PREFIXES: tuple[str, ...] = ("❯ ", "❯")


# Steady-state markers that appear in the status bar AFTER cold-start
# completes. Presence of any of these (alongside the prompt) is a strong
# secondary signal that init has finished. We treat them as a positive
# corroborator — the probe still primarily relies on banner-gone +
# prompt-present, but the model-name check filters out an otherwise
# pathological case where an empty box + cursor would otherwise look
# ready.
CLAUDE_STEADY_MARKERS: tuple[str, ...] = (
    "Sonnet",
    "Haiku",
    "Opus",
)


class ClaudeInitProbe:
    """Probe Claude Code TUI for 'input box ready' state.

    Implements InitGateProbe protocol for use with InitGate.

    Ready criteria (AND — all must be true):
        S1: setup-wizard / welcome banner strings NOT present in visible pane
        S2: input prompt prefix (``❯``) appears on at least one line near
            the bottom of the visible pane (the prompt sits inside the
            input box which has separator lines above/below)
        S3: a steady-state marker (Sonnet / Haiku / Opus) is present —
            corroborates that the box is fully rendered

    Uses visible-screen-only capture (no scrollback) to avoid false
    positives from historical banners. Stability (S4) is layered on top
    by InitGate via consecutive-true counting; the probe itself is
    stateless.
    """

    def __init__(
        self,
        *,
        pane_id: str,
        tmux_run_fn: Callable[[list[str]], str],
    ) -> None:
        """Initialize probe.

        Args:
            pane_id: Tmux pane identifier (e.g., "%2")
            tmux_run_fn: Callable that runs tmux command and returns stdout.
                         Expected signature: (args: list[str]) -> str
        """
        self._pane_id = pane_id
        self._tmux_run = tmux_run_fn

    def detect(self) -> bool:
        """Return True if Claude Code TUI is ready for input.

        Conservative: any error or ambiguity returns False.
        """
        try:
            capture = self._capture_visible()
        except Exception:
            return False

        return (
            self._banner_gone(capture)
            and self._prompt_present(capture)
            and self._steady_marker_present(capture)
        )

    def _capture_visible(self) -> str:
        """Capture visible screen (no scrollback) from pane."""
        args = ["capture-pane", "-p", "-t", self._pane_id]
        return self._tmux_run(args)

    def _banner_gone(self, capture: str) -> bool:
        """S1: Check that welcome / setup banner strings are NOT present."""
        capture_lower = capture.lower()
        for banner in CLAUDE_INIT_BANNERS:
            if banner.lower() in capture_lower:
                return False
        return True

    def _prompt_present(self, capture: str) -> bool:
        """S2: Check that the input prompt prefix appears in the bottom region.

        The Claude prompt sits inside an input-box decoration, so it is
        not necessarily on the very last visible line. Look in the last
        ~8 non-empty lines.
        """
        lines = [ln for ln in capture.splitlines() if ln.strip()]
        if not lines:
            return False
        tail = lines[-8:]
        for line in tail:
            stripped = line.lstrip()
            if any(stripped.startswith(p) for p in CLAUDE_PROMPT_PREFIXES):
                return True
        return False

    def _steady_marker_present(self, capture: str) -> bool:
        """S3: Check that a model-name steady-state marker is present."""
        for marker in CLAUDE_STEADY_MARKERS:
            if marker in capture:
                return True
        return False

    def capture_visible_for_diagnostics(self) -> str:
        """Expose visible capture for InitGate diagnostics."""
        try:
            return self._capture_visible()
        except Exception:
            return ""


__all__ = [
    "CLAUDE_INIT_BANNERS",
    "CLAUDE_PROMPT_PREFIXES",
    "CLAUDE_STEADY_MARKERS",
    "ClaudeInitProbe",
]
