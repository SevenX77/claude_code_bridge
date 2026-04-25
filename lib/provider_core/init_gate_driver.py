"""ccbd-internal driver for per-agent InitGate state machines (Q3 Stage 1b Step 3).

Holds a registry of agent_name -> InitGate, advances each gate via tick()
on every ccbd heartbeat, and exposes state via a read-only ``get_state()``
method backing the ``init_state`` RPC handler.

Design context: Q3 Stage 1b DESIGN §6 ("ccbd-internal driver").

Deliberate scope of Step 3 (per Codex Rubric A 8.2 plan review):
    - This module is the container + tick pump + read API only.
    - The actual hookup that registers gates when an agent mounts lives
      in a follow-up step (Step 3.5 or merged into Step 4 alongside
      tmux_send.py changes), once the precise mount-flow integration
      point has been investigated. Until then, the driver reports
      ``registered=False`` for every agent — by design, not a bug.
"""
from __future__ import annotations

from typing import Any

from provider_core.init_gate import InitGate, InitGateState


class InitGateDriver:
    """Per-agent InitGate registry + tick pump.

    Lifecycle:
        - ``register(agent_name, gate)``: called when an agent is mounted
          and its provider-specific probe has been instantiated.
        - ``tick_all()``: called once per ccbd heartbeat (~0.2s); advances
          gates in INITIALIZING. READY / INIT_FAIL are terminal — kept in
          the registry for query but not re-ticked.
        - ``get_state(agent_name)``: read-only state query for RPC.
        - ``unregister(agent_name)``: called when an agent stops/unmounts.

    Per-gate exception isolation: ``tick_all()`` wraps each gate's
    ``tick()`` in try/except so a single misbehaving probe (e.g. tmux
    transient error) does not block ccbd's heartbeat loop. Failures here
    are converted into INIT_FAIL with reason "tick_exception:<repr>".
    """

    def __init__(self) -> None:
        self._gates: dict[str, InitGate] = {}

    def register(self, agent_name: str, gate: InitGate) -> None:
        """Register a gate for ``agent_name``. Idempotent: replaces existing."""
        self._gates[agent_name] = gate

    def unregister(self, agent_name: str) -> None:
        """Drop the gate for ``agent_name``. No-op if not registered."""
        self._gates.pop(agent_name, None)

    def tick_all(self) -> None:
        """Advance every gate in INITIALIZING; skip terminal states.

        Called from ``CcbdApp.heartbeat()`` once per poll interval
        (~0.2s). Terminal states (READY, INIT_FAIL) and the LAUNCHED
        prologue are handled by ``InitGate.tick()`` itself; this driver
        simply pumps. Per-gate exceptions are caught and converted to
        INIT_FAIL on that gate alone.
        """
        for agent_name, gate in self._gates.items():
            if gate.state in (InitGateState.READY, InitGateState.INIT_FAIL):
                continue
            try:
                gate.tick()
            except Exception as exc:
                # Convert tick exception into INIT_FAIL on this gate only.
                # Do not propagate — ccbd heartbeat must keep running for
                # other agents and other supervised subsystems.
                gate.force_fail(f"tick_exception:{exc!r}")

    def get_state(self, agent_name: str) -> dict[str, Any]:
        """Return read-only state for ``agent_name``.

        Schema (always returned, never raises):
            {
                "agent_name": str,
                "registered": bool,
                "state": str | None,            # InitGateState.name; None if not_registered
                "ready": bool,                  # True iff state == "READY"
                "failed": bool,                 # True iff state == "INIT_FAIL"
                "failure_reason": str | None,   # populated when failed
            }
        """
        gate = self._gates.get(agent_name)
        if gate is None:
            return {
                "agent_name": agent_name,
                "registered": False,
                "state": None,
                "ready": False,
                "failed": False,
                "failure_reason": None,
            }
        state = gate.state
        ready = state == InitGateState.READY
        failed = state == InitGateState.INIT_FAIL
        return {
            "agent_name": agent_name,
            "registered": True,
            "state": state.name,
            "ready": ready,
            "failed": failed,
            "failure_reason": gate.last_reason if failed else None,
        }

    def is_ready(self, agent_name: str) -> bool:
        """Convenience shortcut: True iff agent is registered AND READY.

        Used by callers that only care about the binary ready/not-ready
        decision (e.g. ``tmux_send.py`` Step 4 fast path).
        """
        gate = self._gates.get(agent_name)
        return gate is not None and gate.state == InitGateState.READY

    def registered_count(self) -> int:
        """Number of agents currently in the driver (any state)."""
        return len(self._gates)
