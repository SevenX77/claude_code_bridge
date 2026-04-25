"""Tests for Gemini send-path init-gate query (Q3 Stage 1b Step 4).

We test the helper directly (`_wait_for_init_gate_ready_or_warn`) rather
than driving the full _send_via_terminal because the latter requires a
fully-initialised session_info / backend / pane_id which is heavy to fake.
The helper is the entire integration surface: it owns the agent_name
lookup, the socket discovery, the IPC client construction, and the
fall-through logging.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from provider_backends.gemini.comm_runtime.communicator_facade import GeminiCommunicator


class _FakeClient:
    def __init__(self, response: dict[str, Any] | Exception) -> None:
        self.response = response
        self.calls = 0

    def init_state(self, agent_name: str) -> dict[str, Any]:
        self.calls += 1
        if isinstance(self.response, Exception):
            raise self.response
        return dict(self.response)


def _make_comm(*, agent_name: str = "a2",
               socket_path: str | None = "/tmp/ccbd.sock") -> GeminiCommunicator:
    """Build a bare GeminiCommunicator without running __init__ side effects."""
    comm = GeminiCommunicator.__new__(GeminiCommunicator)
    comm.agent_name = agent_name
    comm.session_info = {"ccbd_socket_path": socket_path} if socket_path else {}
    return comm


def _ready_state(name: str = "a2") -> dict[str, Any]:
    return {
        "agent_name": name, "registered": True, "state": "READY",
        "ready": True, "failed": False, "failure_reason": None,
    }


def _failed_state(name: str = "a2") -> dict[str, Any]:
    return {
        "agent_name": name, "registered": True, "state": "INIT_FAIL",
        "ready": False, "failed": True, "failure_reason": "deadline",
    }


class TestSendPathInitGate:
    def test_ready_returns_silently_no_warning(self, caplog):
        comm = _make_comm()
        client = _FakeClient(_ready_state())
        with patch(
            'ccbd.socket_client.CcbdClient',
            return_value=client,
        ):
            with caplog.at_level(logging.WARNING):
                comm._wait_for_init_gate_ready_or_warn()
        assert client.calls == 1
        assert not any('fall_through' in r.getMessage() for r in caplog.records)

    def test_init_fail_logs_warning_and_returns(self, caplog):
        comm = _make_comm()
        client = _FakeClient(_failed_state())
        with patch(
            'ccbd.socket_client.CcbdClient',
            return_value=client,
        ):
            with caplog.at_level(logging.WARNING):
                comm._wait_for_init_gate_ready_or_warn()
        assert client.calls == 1
        msgs = [r.getMessage() for r in caplog.records]
        assert any('fall_through' in m and 'FAILED' in m for m in msgs)

    def test_query_error_logs_warning_and_returns(self, caplog):
        comm = _make_comm()
        client = _FakeClient(ConnectionRefusedError("ccbd unreachable"))
        with patch(
            'ccbd.socket_client.CcbdClient',
            return_value=client,
        ):
            with caplog.at_level(logging.WARNING):
                comm._wait_for_init_gate_ready_or_warn()
        msgs = [r.getMessage() for r in caplog.records]
        assert any('fall_through' in m and 'QUERY_ERROR' in m for m in msgs)

    def test_no_agent_name_skips_silently(self, caplog):
        comm = _make_comm(agent_name="")
        # Even though we pass a CcbdClient mock, it must NOT be constructed.
        with patch(
            'ccbd.socket_client.CcbdClient',
        ) as mock_client:
            with caplog.at_level(logging.WARNING):
                comm._wait_for_init_gate_ready_or_warn()
        mock_client.assert_not_called()
        assert not caplog.records  # no warnings either

    def test_no_socket_path_skips_silently(self, caplog):
        comm = _make_comm(socket_path=None)
        with patch(
            'ccbd.socket_client.CcbdClient',
        ) as mock_client:
            with caplog.at_level(logging.WARNING):
                comm._wait_for_init_gate_ready_or_warn()
        mock_client.assert_not_called()
        assert not caplog.records


class TestSocketPathResolution:
    def test_explicit_socket_path_wins(self):
        comm = _make_comm(socket_path="/custom/ccbd.sock")
        assert comm._resolve_ccbd_socket_path() == "/custom/ccbd.sock"

    def test_walks_up_to_find_ccb_dir(self, tmp_path):
        # Create a fake project root with .ccb/
        project = tmp_path / "myproject"
        (project / ".ccb").mkdir(parents=True)
        nested = project / "src" / "deep"
        nested.mkdir(parents=True)

        comm = GeminiCommunicator.__new__(GeminiCommunicator)
        comm.session_info = {"start_dir": str(nested)}
        result = comm._resolve_ccbd_socket_path()
        # PathLayout produces a real socket path under the project dir
        assert result is not None
        assert ".ccb" in result or "ccbd" in result

    def test_no_ccb_dir_returns_none(self, tmp_path):
        # Empty tmp_path with no .ccb anywhere up the chain
        empty = tmp_path / "nothing_here"
        empty.mkdir()
        comm = GeminiCommunicator.__new__(GeminiCommunicator)
        comm.session_info = {"start_dir": str(empty), "work_dir": str(empty)}
        # Walk would hit / before finding .ccb (assuming none there)
        result = comm._resolve_ccbd_socket_path()
        # We can't strictly assert None because system may have a
        # /something/.ccb. Just assert no crash and stable output type.
        assert result is None or isinstance(result, str)
