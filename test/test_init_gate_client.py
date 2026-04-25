"""Unit tests for init_gate_client.wait_for_init_ready (Q3 Stage 1b Step 4)."""
from __future__ import annotations

from typing import Any

import pytest

from provider_core.init_gate_client import InitGateOutcome, wait_for_init_ready


class _FakeClient:
    """Returns a scripted sequence of init_state responses."""

    def __init__(self, responses: list[dict[str, Any] | Exception]) -> None:
        self._responses = list(responses)
        self._idx = 0
        self.calls = 0

    def init_state(self, agent_name: str) -> dict[str, Any]:
        self.calls += 1
        if self._idx < len(self._responses):
            value = self._responses[self._idx]
            self._idx += 1
        else:
            value = self._responses[-1]
        if isinstance(value, Exception):
            raise value
        return dict(value)


class _Clock:
    """Manual clock + sleep tracker for deterministic poll loops."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, delta: float) -> None:
        self.sleeps.append(delta)
        self.now += delta


def _state(*, registered: bool = True, state: str | None = "INITIALIZING",
           ready: bool = False, failed: bool = False,
           failure_reason: str | None = None) -> dict[str, Any]:
    return {
        "agent_name": "a1",
        "registered": registered,
        "state": state,
        "ready": ready,
        "failed": failed,
        "failure_reason": failure_reason,
    }


class TestWaitForInitReady:
    def test_immediate_ready_returns_ready(self):
        client = _FakeClient([_state(state="READY", ready=True)])
        clock = _Clock()
        outcome = wait_for_init_ready(
            client, "a1",
            timeout_s=10.0, poll_interval_s=0.5,
            clock=clock.time, sleep_fn=clock.sleep,
        )
        assert outcome == InitGateOutcome.READY
        assert client.calls == 1
        assert clock.sleeps == []  # READY first try → no sleep

    def test_initializing_then_ready(self):
        client = _FakeClient([
            _state(state="INITIALIZING"),
            _state(state="INITIALIZING"),
            _state(state="READY", ready=True),
        ])
        clock = _Clock()
        outcome = wait_for_init_ready(
            client, "a1",
            timeout_s=10.0, poll_interval_s=0.5,
            clock=clock.time, sleep_fn=clock.sleep,
        )
        assert outcome == InitGateOutcome.READY
        assert client.calls == 3
        assert clock.sleeps == [0.5, 0.5]  # two sleeps before READY

    def test_init_fail_returns_failed_immediately(self):
        client = _FakeClient([
            _state(state="INIT_FAIL", failed=True, failure_reason="deadline"),
        ])
        clock = _Clock()
        outcome = wait_for_init_ready(
            client, "a1",
            timeout_s=10.0, poll_interval_s=0.5,
            clock=clock.time, sleep_fn=clock.sleep,
        )
        assert outcome == InitGateOutcome.FAILED
        assert client.calls == 1

    def test_timeout_when_stuck_initializing(self):
        # Always INITIALIZING, never READY/FAILED → must hit timeout
        client = _FakeClient([_state(state="INITIALIZING")])
        clock = _Clock()
        outcome = wait_for_init_ready(
            client, "a1",
            timeout_s=2.0, poll_interval_s=0.5,
            clock=clock.time, sleep_fn=clock.sleep,
        )
        assert outcome == InitGateOutcome.TIMEOUT
        # Should have done at least 4 polls before tripping the deadline
        assert client.calls >= 4

    def test_not_registered_throughout_returns_not_registered(self):
        # registered=False forever → not_registered after timeout
        client = _FakeClient([_state(registered=False, state=None)])
        clock = _Clock()
        outcome = wait_for_init_ready(
            client, "a1",
            timeout_s=1.0, poll_interval_s=0.4,
            clock=clock.time, sleep_fn=clock.sleep,
        )
        assert outcome == InitGateOutcome.NOT_REGISTERED

    def test_query_error_after_three_consecutive_failures(self):
        client = _FakeClient([
            ConnectionRefusedError("ccbd down"),
            ConnectionRefusedError("ccbd down"),
            ConnectionRefusedError("ccbd down"),
        ])
        clock = _Clock()
        outcome = wait_for_init_ready(
            client, "a1",
            timeout_s=10.0, poll_interval_s=0.1,
            clock=clock.time, sleep_fn=clock.sleep,
        )
        assert outcome == InitGateOutcome.QUERY_ERROR
        assert client.calls == 3

    def test_recovers_after_transient_query_error(self):
        # 2 errors then a real READY — must NOT trip QUERY_ERROR
        client = _FakeClient([
            ConnectionRefusedError("transient"),
            ConnectionRefusedError("transient"),
            _state(state="READY", ready=True),
        ])
        clock = _Clock()
        outcome = wait_for_init_ready(
            client, "a1",
            timeout_s=10.0, poll_interval_s=0.1,
            clock=clock.time, sleep_fn=clock.sleep,
        )
        assert outcome == InitGateOutcome.READY
        assert client.calls == 3
