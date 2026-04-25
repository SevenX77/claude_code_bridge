"""Tests for Claude send-path init-gate query (Q3 Stage 1c).

Mirrors test_gemini_send_path_init_gate.py.
"""
from __future__ import annotations

import logging
from typing import Any
from unittest.mock import patch

import pytest

from provider_backends.claude.comm_runtime.communicator_facade import ClaudeCommunicator


class _FakeClient:
    def __init__(self, response: dict[str, Any] | Exception) -> None:
        self.response = response
        self.calls = 0

    def init_state(self, agent_name: str) -> dict[str, Any]:
        self.calls += 1
        if isinstance(self.response, Exception):
            raise self.response
        return dict(self.response)


def _make_comm(*, agent_name: str = "a3",
               socket_path: str | None = "/tmp/ccbd.sock") -> ClaudeCommunicator:
    """Build a bare ClaudeCommunicator without running __init__ side effects."""
    comm = ClaudeCommunicator.__new__(ClaudeCommunicator)
    comm.agent_name = agent_name
    comm.session_info = {"ccbd_socket_path": socket_path} if socket_path else {}
    return comm


def _ready_state(name: str = "a3") -> dict[str, Any]:
    return {
        "agent_name": name, "registered": True, "state": "READY",
        "ready": True, "failed": False, "failure_reason": None,
    }


def _failed_state(name: str = "a3") -> dict[str, Any]:
    return {
        "agent_name": name, "registered": True, "state": "INIT_FAIL",
        "ready": False, "failed": True, "failure_reason": "deadline",
    }


class TestSendPathInitGate:
    def test_ready_returns_silently_no_warning(self, caplog):
        comm = _make_comm()
        client = _FakeClient(_ready_state())
        with patch('ccbd.socket_client.CcbdClient', return_value=client):
            with caplog.at_level(logging.WARNING):
                comm._wait_for_init_gate_ready_or_warn()
        assert client.calls == 1
        assert not any('fall_through' in r.getMessage() for r in caplog.records)

    def test_init_fail_logs_warning_and_returns(self, caplog):
        comm = _make_comm()
        client = _FakeClient(_failed_state())
        with patch('ccbd.socket_client.CcbdClient', return_value=client):
            with caplog.at_level(logging.WARNING):
                comm._wait_for_init_gate_ready_or_warn()
        assert client.calls == 1
        msgs = [r.getMessage() for r in caplog.records]
        assert any('fall_through' in m and 'FAILED' in m for m in msgs)

    def test_query_error_logs_warning_and_returns(self, caplog):
        comm = _make_comm()
        client = _FakeClient(ConnectionRefusedError("ccbd unreachable"))
        with patch('ccbd.socket_client.CcbdClient', return_value=client):
            with caplog.at_level(logging.WARNING):
                comm._wait_for_init_gate_ready_or_warn()
        msgs = [r.getMessage() for r in caplog.records]
        assert any('fall_through' in m and 'QUERY_ERROR' in m for m in msgs)

    def test_no_agent_name_skips_silently(self, caplog):
        comm = _make_comm(agent_name="")
        with patch('ccbd.socket_client.CcbdClient') as mock_client:
            with caplog.at_level(logging.WARNING):
                comm._wait_for_init_gate_ready_or_warn()
        mock_client.assert_not_called()
        assert not caplog.records

    def test_no_socket_path_skips_silently(self, caplog):
        comm = _make_comm(socket_path=None)
        with patch('ccbd.socket_client.CcbdClient') as mock_client:
            with caplog.at_level(logging.WARNING):
                comm._wait_for_init_gate_ready_or_warn()
        mock_client.assert_not_called()
        assert not caplog.records


class TestSocketPathResolution:
    def test_explicit_socket_path_wins(self):
        comm = _make_comm(socket_path="/custom/ccbd.sock")
        assert comm._resolve_ccbd_socket_path() == "/custom/ccbd.sock"

    def test_walks_up_to_find_ccb_dir(self, tmp_path):
        project = tmp_path / "myproject"
        (project / ".ccb").mkdir(parents=True)
        nested = project / "src" / "deep"
        nested.mkdir(parents=True)
        comm = ClaudeCommunicator.__new__(ClaudeCommunicator)
        comm.session_info = {"start_dir": str(nested)}
        result = comm._resolve_ccbd_socket_path()
        assert result is not None
        assert ".ccb" in result or "ccbd" in result
