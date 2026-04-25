"""Per-provider InitGate registration factory (Q3 Stage 1b Step 4).

Called from ``mount_agent_from_policy`` / ``remount_project_from_policy``
after an agent is successfully started. Builds a provider-specific
``InitGate`` and registers it with ``app.init_gate_driver`` so the
ccbd heartbeat starts ticking it on next iteration.

Provider dispatch:
    - "gemini" → builds ``GeminiInitProbe`` + ``InitGate`` + registers.
    - "codex"  → no-op. Codex owns its gate inside the per-agent bridge
                 process (see ``provider_backends.codex.bridge_runtime.runtime_state``).
    - "claude" → no-op. Stage 1c will add ``ClaudeInitProbe`` here.
    - others   → no-op + log warning.

The factory is intentionally provider-aware in ONE place so
``run_start_flow`` and ``policy.py`` stay provider-agnostic.
"""
from __future__ import annotations

import logging
from typing import Any

from provider_core.init_gate import InitGate, load_init_gate_env

logger = logging.getLogger(__name__)


def register_provider_init_gate_for_agent(app: Any, agent_name: str) -> bool:
    """Build + register an InitGate for ``agent_name`` if its provider is supported.

    Returns:
        True iff a gate was registered. False on no-op (unknown provider,
        unsupported provider, missing pane_id, missing runtime).

    Never raises: registration is best-effort. Failures here must NOT
    break the mount flow — the agent is already started, the gate is an
    optimisation layer, and the existing reception-driven retry remains
    the last line of defence.
    """
    try:
        runtime = app.registry.get(agent_name)
    except Exception as exc:
        logger.warning(
            "init_gate_register: action=lookup_failed agent=%s reason=%r",
            agent_name, exc,
        )
        return False
    if runtime is None:
        logger.info(
            "init_gate_register: action=skip_no_runtime agent=%s", agent_name,
        )
        return False

    provider = (runtime.provider or "").strip().lower()
    if provider == "codex":
        # Codex's bridge process owns its InitGate; ccbd-side gate would
        # double up. Codex's send path does not query init_state RPC.
        return False
    if provider == "claude":
        # Stage 1c will land ClaudeInitProbe; until then this is a no-op.
        logger.debug(
            "init_gate_register: action=skip_claude_pending_stage_1c agent=%s",
            agent_name,
        )
        return False
    if provider != "gemini":
        logger.warning(
            "init_gate_register: action=skip_unknown_provider agent=%s provider=%s",
            agent_name, provider,
        )
        return False

    pane_id = (runtime.pane_id or "").strip()
    if not pane_id:
        logger.warning(
            "init_gate_register: action=skip_no_pane_id agent=%s",
            agent_name,
        )
        return False

    try:
        gate = _build_gemini_gate(app=app, agent_name=agent_name, runtime=runtime)
    except Exception as exc:
        logger.warning(
            "init_gate_register: action=build_failed agent=%s reason=%r",
            agent_name, exc,
        )
        return False

    try:
        app.init_gate_driver.register(agent_name, gate)
    except Exception as exc:
        logger.warning(
            "init_gate_register: action=driver_register_failed agent=%s reason=%r",
            agent_name, exc,
        )
        return False

    logger.info(
        "init_gate_register: action=registered agent=%s provider=gemini pane=%s",
        agent_name, pane_id,
    )
    return True


def register_provider_init_gates_for_started(app: Any, agent_names) -> tuple[str, ...]:
    """Bulk-register newly-started agents. Returns names that registered."""
    registered: list[str] = []
    for name in agent_names:
        try:
            if register_provider_init_gate_for_agent(app, name):
                registered.append(name)
        except Exception as exc:
            # Defence in depth: factory is already best-effort, but loop
            # must not abort if an unexpected error escapes.
            logger.warning(
                "init_gate_register: action=loop_unhandled agent=%s reason=%r",
                name, exc,
            )
    return tuple(registered)


def _build_gemini_gate(*, app: Any, agent_name: str, runtime) -> InitGate:
    """Construct InitGate(probe=GeminiInitProbe, ...) for one Gemini agent."""
    # Late import: provider_backends modules pull in heavy deps; only load
    # when actually constructing a Gemini gate.
    from provider_backends.gemini.init_probe import GeminiInitProbe
    from terminal_runtime import TmuxBackend

    backend = TmuxBackend(
        socket_name=runtime.tmux_socket_name,
        socket_path=runtime.tmux_socket_path,
    )

    def _tmux_run_str(args: list[str]) -> str:
        try:
            cp = backend._tmux_run(args, capture=True, timeout=2.0, check=False)
        except Exception:
            return ""
        if cp is None:
            return ""
        out = getattr(cp, "stdout", "")
        if isinstance(out, bytes):
            try:
                return out.decode("utf-8", errors="replace")
            except Exception:
                return ""
        return out if isinstance(out, str) else ""

    probe = GeminiInitProbe(pane_id=runtime.pane_id, tmux_run_fn=_tmux_run_str)

    runtime_dir = app.paths.agent_provider_runtime_dir(agent_name, "gemini")
    runtime_dir.mkdir(parents=True, exist_ok=True)

    def _capture() -> str:
        try:
            return probe.capture_visible_for_diagnostics()
        except Exception:
            return ""

    gate_kwargs = load_init_gate_env("gemini")
    return InitGate(
        probe=probe,
        provider="gemini",
        runtime_dir=runtime_dir,
        capture_fn=_capture,
        **gate_kwargs,
    )


__all__ = [
    "register_provider_init_gate_for_agent",
    "register_provider_init_gates_for_started",
]
