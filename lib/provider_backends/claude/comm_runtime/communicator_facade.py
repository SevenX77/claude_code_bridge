from __future__ import annotations

import logging
from pathlib import Path

from provider_core.init_gate_client import InitGateOutcome, wait_for_init_ready
from provider_core.protocol import is_done_text, make_req_id, strip_done_text
from terminal_runtime import get_backend_for_session, get_pane_id_from_session

from ..protocol import wrap_claude_prompt
from ..resolver import resolve_claude_session
from . import (
    ask_async as _ask_async_impl,
    ask_sync as _ask_sync_impl,
    check_session_health as _check_session_health_impl,
    ensure_log_reader as _ensure_log_reader_impl,
    initialize_state as _initialize_comm_state,
    ping as _ping_impl,
    prime_log_binding as _prime_log_binding_impl,
    publish_claude_registry,
    publish_registry as _publish_registry_impl,
    remember_claude_session as _remember_claude_session_impl,
    remember_claude_session_binding,
)

logger = logging.getLogger(__name__)


def _claude_log_reader_cls():
    from .. import comm as claude_comm_module

    return claude_comm_module.ClaudeLogReader


class ClaudeCommunicator:
    """Communicate with Claude via terminal and read replies from session logs."""

    def __init__(self, lazy_init: bool = False):
        _initialize_comm_state(
            self,
            get_backend_for_session_fn=get_backend_for_session,
            get_pane_id_from_session_fn=get_pane_id_from_session,
        )

        self._publish_registry()

        if not lazy_init:
            self._ensure_log_reader()
            healthy, msg = self._check_session_health()
            if not healthy:
                raise RuntimeError(
                    "❌ Session unhealthy: "
                    f"{msg}\nHint: run ccb claude (or add claude to ccb.config) to start a new session"
                )

    @property
    def log_reader(self):
        if self._log_reader is None:
            self._ensure_log_reader()
        return self._log_reader

    def _ensure_log_reader(self) -> None:
        _ensure_log_reader_impl(self, log_reader_cls=_claude_log_reader_cls())

    def _load_session_info(self) -> dict | None:
        work_dir = Path.cwd()
        resolution = resolve_claude_session(work_dir)
        if not resolution:
            return None
        data = dict(resolution.data or {})
        if not data:
            return None
        if data.get("active") is False:
            return None
        session_file = resolution.session_file
        if session_file:
            data["_session_file"] = str(session_file)
        data["work_dir"] = str(Path(data.get("work_dir") or work_dir))
        return data

    def _prime_log_binding(self) -> None:
        _prime_log_binding_impl(self)

    def _check_session_health(self) -> tuple[bool, str]:
        return self._check_session_health_impl(probe_terminal=True)

    def _check_session_health_impl(self, probe_terminal: bool) -> tuple[bool, str]:
        return _check_session_health_impl(self, probe_terminal=probe_terminal)

    def _send_via_terminal(self, content: str) -> bool:
        if not self.backend or not self.pane_id:
            raise RuntimeError("Terminal session not configured")
        # Q3 Stage 1c: ask ccbd whether the agent's TUI is ready before
        # pasting. READY → fast path. Anything else (FAILED /
        # NOT_REGISTERED / TIMEOUT / QUERY_ERROR) → log + fall through to
        # whatever the existing send pipeline does. Mirror of Gemini Step 4.
        self._wait_for_init_gate_ready_or_warn()
        self.backend.send_text(self.pane_id, content)
        return True

    def _wait_for_init_gate_ready_or_warn(self) -> None:
        """Best-effort init-gate query before paste. See Gemini Step 4 docs."""
        agent_name = (getattr(self, "agent_name", "") or "").strip()
        if not agent_name:
            return
        socket_path = self._resolve_ccbd_socket_path()
        if socket_path is None:
            return
        try:
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
        logger.warning(
            "init_gate: action=fall_through agent=%s outcome=%s; "
            "downstream send pipeline will validate delivery",
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

    def _remember_claude_session(self, session_path: Path) -> None:
        _remember_claude_session_impl(
            self,
            session_path,
            remember_claude_session_binding_fn=remember_claude_session_binding,
        )

    def _publish_registry(self) -> None:
        _publish_registry_impl(
            self,
            publish_claude_registry_fn=publish_claude_registry,
        )

    def ask_async(self, question: str) -> bool:
        return _ask_async_impl(self, question)

    def ask_sync(self, question: str, timeout: int | None = None) -> str | None:
        return _ask_sync_impl(
            self,
            question,
            timeout=timeout,
            req_id_factory=make_req_id,
            wrap_prompt_fn=wrap_claude_prompt,
            is_done_text_fn=is_done_text,
            strip_done_text_fn=strip_done_text,
        )

    def ping(self, display: bool = True) -> tuple[bool, str]:
        return _ping_impl(self, display=display)


__all__ = ["ClaudeCommunicator"]
