"""Client-side polling helper for InitGateDriver state queries (Q3 Stage 1b Step 4).

Used by provider communicators (e.g. ``GeminiCommunicator._send_via_terminal``)
to wait for an agent's init gate to reach READY before pasting the first
message into a freshly-mounted TUI. Falls through to caller's existing
delivery logic on FAILED / TIMEOUT / NOT_REGISTERED so init-gate failure
never hard-blocks message delivery (the reception-driven retry is the
last line of defence — see Gemini design review notes in commit body).

Future work (Q4 Circuit-breaker, NOT this step): the "Probe-and-Seal"
pattern (Gemini round-2 design) — first INIT_FAIL triggers a 20s × 1
aggressive probe; success → mark agent PROBE_BYPASSED; failure → mark
agent DEAD and fail-fast all subsequent sends. Captured here as a TODO
because the bypass/dead state needs to live server-side (CLI processes
are per-invocation), and that fits naturally in Q4's circuit-breaker
package.
"""
from __future__ import annotations

import os
import time
from enum import Enum, auto
from typing import Any, Callable, Protocol


class InitGateOutcome(Enum):
    """Result of waiting for an agent's init gate state."""

    READY = auto()           # gate.state == READY
    FAILED = auto()          # gate.state == INIT_FAIL
    NOT_REGISTERED = auto()  # gate never appeared in driver before deadline
    TIMEOUT = auto()         # gate stayed INITIALIZING / LAUNCHED past deadline
    QUERY_ERROR = auto()     # IPC repeatedly raised; conservative fall-through


class _InitStateClient(Protocol):
    """Minimal interface a CcbdClient (or test fake) must satisfy."""

    def init_state(self, agent_name: str) -> dict[str, Any]: ...


def wait_for_init_ready(
    client: _InitStateClient,
    agent_name: str,
    *,
    timeout_s: float | None = None,
    poll_interval_s: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> InitGateOutcome:
    """Poll ``client.init_state(agent_name)`` until terminal or deadline.

    Args:
        client: Object exposing ``init_state(agent_name) -> dict`` (typically
            ``CcbdClient`` or a test fake).
        agent_name: Agent to query.
        timeout_s: Total deadline. Default from
            ``CCB_INIT_GATE_CLIENT_TIMEOUT_S`` (env), else 30.0.
        poll_interval_s: Sleep between polls. Default from
            ``CCB_INIT_GATE_CLIENT_POLL_INTERVAL_S`` (env), else 0.5.
        clock / sleep_fn: Injectable for tests.

    Returns:
        ``InitGateOutcome`` enum. Caller decides how to handle each.
        Never raises; QUERY_ERROR signals "couldn't determine state".
    """
    timeout_s = _resolve_float_env(
        timeout_s, "CCB_INIT_GATE_CLIENT_TIMEOUT_S", 30.0
    )
    poll_interval_s = _resolve_float_env(
        poll_interval_s, "CCB_INIT_GATE_CLIENT_POLL_INTERVAL_S", 0.5
    )

    deadline = clock() + max(0.0, timeout_s)
    consecutive_query_errors = 0
    saw_registered = False

    while True:
        try:
            state = client.init_state(agent_name)
            consecutive_query_errors = 0
        except Exception:
            consecutive_query_errors += 1
            # 3 consecutive IPC failures within deadline → give up querying.
            if consecutive_query_errors >= 3:
                return InitGateOutcome.QUERY_ERROR
            state = None

        if state is not None:
            registered = bool(state.get("registered"))
            if registered:
                saw_registered = True
            if state.get("ready"):
                return InitGateOutcome.READY
            if state.get("failed"):
                return InitGateOutcome.FAILED

        if clock() >= deadline:
            return (
                InitGateOutcome.TIMEOUT
                if saw_registered
                else InitGateOutcome.NOT_REGISTERED
            )

        sleep_fn(poll_interval_s)


def _resolve_float_env(explicit: float | None, env_name: str, default: float) -> float:
    if explicit is not None:
        try:
            return max(0.0, float(explicit))
        except (TypeError, ValueError):
            return default
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        return default
    try:
        return max(0.0, float(raw))
    except ValueError:
        return default


__all__ = ["InitGateOutcome", "wait_for_init_ready"]
