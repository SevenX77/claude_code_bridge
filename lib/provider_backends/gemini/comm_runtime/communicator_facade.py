from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

from terminal_runtime import get_backend_for_session, get_pane_id_from_session

from ..session import find_project_session_file as find_gemini_project_session_file
from . import (
    ask_async as _ask_async_impl,
    ask_sync as _ask_sync_impl,
    check_session_health as _check_session_health_impl,
    consume_pending as _consume_pending_impl,
    ensure_log_reader as _ensure_log_reader_impl,
    find_gemini_session_file,
    get_status as _get_status_impl,
    initialize_state as _initialize_state,
    load_gemini_session_info,
    prime_log_binding as _prime_log_binding_impl,
    publish_initial_registry_binding as _publish_initial_registry_binding_impl,
    remember_gemini_session as _remember_gemini_session_impl,
    send_message as _send_message_impl,
    update_project_session_binding,
)
from provider_core.init_gate_client import InitGateOutcome, wait_for_init_ready
from provider_hooks.artifacts_runtime.transcript import extract_req_id
from .log_reader_facade import GeminiLogReader

logger = logging.getLogger(__name__)


def _publish_registry_binding_proxy(**kwargs) -> None:
    from .. import comm as gemini_comm_module

    gemini_comm_module.publish_registry_binding(**kwargs)


class GeminiCommunicator:
    """Communicate with Gemini via terminal and read replies from session files."""

    def __init__(self, lazy_init: bool = False):
        _initialize_state(
            self,
            get_pane_id_from_session_fn=get_pane_id_from_session,
            get_backend_for_session_fn=get_backend_for_session,
        )
        _publish_initial_registry_binding_impl(
            self,
            publish_registry_binding_fn=_publish_registry_binding_proxy,
        )

        if not lazy_init:
            self._ensure_log_reader()
            healthy, msg = self._check_session_health()
            if not healthy:
                raise RuntimeError(
                    f"❌ Session unhealthy: {msg}\nHint: Please run ccb gemini (or add gemini to ccb.config)"
                )

    @property
    def log_reader(self) -> GeminiLogReader:
        if self._log_reader is None:
            self._ensure_log_reader()
        return self._log_reader

    def _ensure_log_reader(self) -> None:
        _ensure_log_reader_impl(self, log_reader_cls=GeminiLogReader)

    def _find_session_file(self) -> Path | None:
        return find_gemini_session_file(cwd=Path.cwd(), finder=find_gemini_project_session_file)

    def _prime_log_binding(self) -> None:
        _prime_log_binding_impl(self)

    def _load_session_info(self):
        return load_gemini_session_info(session_finder=self._find_session_file)

    def _check_session_health(self) -> tuple[bool, str]:
        return self._check_session_health_impl(probe_terminal=True)

    def _check_session_health_impl(self, probe_terminal: bool) -> tuple[bool, str]:
        return _check_session_health_impl(self, probe_terminal=probe_terminal)

    def _send_via_terminal(self, content: str) -> bool:
        if not self.backend or not self.pane_id:
            raise RuntimeError("Terminal session not configured")
        # Q3 Stage 1b Step 4: ask ccbd whether the agent's TUI is ready
        # before pasting. READY → fast path. Anything else (FAILED /
        # NOT_REGISTERED / TIMEOUT / QUERY_ERROR) → log + fall through to
        # the existing reception-driven retry, which is the actual line of
        # defence against lost input. The gate is an OPTIMISER, not a gate.
        self._wait_for_init_gate_ready_or_warn()
        req_id = extract_req_id(content)
        reception_dir: Path | None = None
        if req_id:
            session = getattr(self, 'session', None)
            completion_dir = getattr(session, 'completion_dir', None) if session is not None else None
            if completion_dir is not None:
                reception_dir = Path(completion_dir).parent / 'reception'
            else:
                runtime_dir = getattr(self, 'runtime_dir', None)
                if runtime_dir is not None:
                    reception_dir = Path(runtime_dir) / 'reception'
        if reception_dir is not None:
            reception_dir.mkdir(parents=True, exist_ok=True)
        self.backend.send_text(self.pane_id, content, req_id=req_id, reception_dir=reception_dir)
        return True

    def _wait_for_init_gate_ready_or_warn(self) -> None:
        """Best-effort init-gate query before paste.

        Skips silently when prerequisites are missing (no agent_name, no
        ccbd socket path, ccbd unreachable). Logs a WARNING and returns
        when the gate is non-READY so the reception-driven retry can take
        over — never raises, never blocks the send.
        """
        agent_name = (getattr(self, "agent_name", "") or "").strip()
        if not agent_name:
            return
        socket_path = self._resolve_ccbd_socket_path()
        if socket_path is None:
            return

        try:
            # Late import: CcbdClient pulls ccbd request models; only
            # construct one when we have a real query to perform.
            from ccbd.socket_client import CcbdClient
        except Exception:  # pragma: no cover — defensive import guard
            return

        try:
            client = CcbdClient(socket_path)
        except Exception:
            return

        outcome = wait_for_init_ready(client, agent_name)
        if outcome == InitGateOutcome.READY:
            return
        # Non-READY: log so ops can spot stale probe / dead TUI without
        # the send path silently degrading.
        logger.warning(
            "init_gate: action=fall_through agent=%s outcome=%s; "
            "reception-driven retry will validate delivery",
            agent_name, outcome.name,
        )

    def _resolve_ccbd_socket_path(self) -> str | None:
        """Find ccbd socket: explicit field first, then walk-up fallback."""
        info = getattr(self, "session_info", None)
        if not isinstance(info, dict):
            return None
        explicit = str(info.get("ccbd_socket_path") or "").strip()
        if explicit:
            return explicit
        # Backward-compat: session payloads written by older launchers
        # don't carry ccbd_socket_path. Derive from start_dir / work_dir
        # by walking up to find a .ccb directory.
        for key in ("start_dir", "work_dir"):
            candidate = info.get(key)
            if not candidate:
                continue
            p = Path(str(candidate))
            for ancestor in [p, *p.parents]:
                if (ancestor / ".ccb").is_dir():
                    try:
                        from storage.paths import PathLayout
                        return str(PathLayout(ancestor).ccbd_socket_path)
                    except Exception:
                        return None
        return None

    def _send_message(self, content: str) -> tuple[str, dict[str, Any]]:
        return _send_message_impl(self, content)

    def _generate_marker(self) -> str:
        return f"{self.marker_prefix}-{int(time.time())}-{os.getpid()}"

    def ask_async(self, question: str) -> bool:
        return _ask_async_impl(self, question)

    def ask_sync(self, question: str, timeout: int | None = None) -> str | None:
        return _ask_sync_impl(self, question, timeout=timeout)

    def consume_pending(self, display: bool = True, n: int = 1):
        return _consume_pending_impl(self, display=display, n=n)

    def _remember_gemini_session(self, session_path: Path) -> None:
        _remember_gemini_session_impl(
            self,
            session_path,
            update_project_session_binding_fn=update_project_session_binding,
            publish_registry_binding_fn=_publish_registry_binding_proxy,
        )

    def ping(self, display: bool = True) -> tuple[bool, str]:
        healthy, status = self._check_session_health()
        msg = f"✅ Gemini connection OK ({status})" if healthy else f"❌ Gemini connection error: {status}"
        if display:
            print(msg)
        return healthy, msg

    def get_status(self) -> dict[str, Any]:
        return _get_status_impl(self)


__all__ = ["GeminiCommunicator"]
