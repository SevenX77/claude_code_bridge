"""Unit tests for InitGateDriver (Q3 Stage 1b Step 3)."""
from __future__ import annotations

from typing import Any

import pytest

from provider_core.init_gate import InitGate, InitGateProbe, InitGateState
from provider_core.init_gate_driver import InitGateDriver


class FakeProbe(InitGateProbe):
    """Returns a scripted boolean sequence; remembers call count."""

    def __init__(self, sequence: list[bool]) -> None:
        self._sequence = list(sequence)
        self._idx = 0
        self.call_count = 0

    def detect(self) -> bool:
        self.call_count += 1
        if self._idx < len(self._sequence):
            value = self._sequence[self._idx]
            self._idx += 1
            return value
        return self._sequence[-1] if self._sequence else False


class RaisingProbe(InitGateProbe):
    """Raises on every detect — simulates broken probe."""

    def detect(self) -> bool:
        raise RuntimeError("simulated probe failure")


def _build_gate(
    *,
    probe: InitGateProbe,
    provider: str = "test",
    runtime_dir: str = "/tmp/test_init_gate_driver",
    deadline_s: float = 30.0,
    poll_fast_ms: int = 1,
    poll_slow_ms: int = 1,
    poll_switch_s: float = 5.0,
    steady_count: int = 2,
    bypass: bool = False,
) -> InitGate:
    """Build a fully-defaulted InitGate for tests."""
    import os
    os.makedirs(runtime_dir, exist_ok=True)
    return InitGate(
        probe=probe,
        provider=provider,
        runtime_dir=runtime_dir,
        capture_fn=lambda: "",
        deadline_s=deadline_s,
        poll_fast_ms=poll_fast_ms,
        poll_slow_ms=poll_slow_ms,
        poll_switch_s=poll_switch_s,
        steady_count=steady_count,
        bypass=bypass,
    )


# ---------- registration ----------


class TestRegistration:
    def test_register_adds_gate(self):
        driver = InitGateDriver()
        gate = _build_gate(probe=FakeProbe([True]))
        driver.register("a1", gate)
        assert driver.registered_count() == 1

    def test_register_replaces_existing(self):
        driver = InitGateDriver()
        gate1 = _build_gate(probe=FakeProbe([True]))
        gate2 = _build_gate(probe=FakeProbe([False]))
        driver.register("a1", gate1)
        driver.register("a1", gate2)
        assert driver.registered_count() == 1
        # The second gate is the active one
        assert driver._gates["a1"] is gate2

    def test_unregister_removes_gate(self):
        driver = InitGateDriver()
        gate = _build_gate(probe=FakeProbe([True]))
        driver.register("a1", gate)
        driver.unregister("a1")
        assert driver.registered_count() == 0

    def test_unregister_unknown_is_noop(self):
        driver = InitGateDriver()
        # Must not raise
        driver.unregister("never-registered")
        assert driver.registered_count() == 0


# ---------- get_state ----------


class TestGetState:
    def test_unknown_agent_returns_not_registered(self):
        driver = InitGateDriver()
        result = driver.get_state("ghost")
        assert result == {
            "agent_name": "ghost",
            "registered": False,
            "state": None,
            "ready": False,
            "failed": False,
            "failure_reason": None,
        }

    def test_initializing_agent_returns_initializing(self):
        driver = InitGateDriver()
        gate = _build_gate(probe=FakeProbe([False]))
        driver.register("a1", gate)
        gate.tick()  # LAUNCHED -> INITIALIZING (one step)
        result = driver.get_state("a1")
        assert result["registered"] is True
        assert result["state"] == "INITIALIZING"
        assert result["ready"] is False
        assert result["failed"] is False
        assert result["failure_reason"] is None

    def test_ready_agent_returns_ready(self):
        driver = InitGateDriver()
        # steady_count=2 → need 2 consecutive Trues
        gate = _build_gate(probe=FakeProbe([True, True, True]), steady_count=2)
        driver.register("a1", gate)
        # tick 1: LAUNCHED -> INITIALIZING
        # tick 2,3: probe True twice consecutively -> READY
        for _ in range(4):
            if gate.tick() == InitGateState.READY:
                break
        result = driver.get_state("a1")
        assert result["state"] == "READY"
        assert result["ready"] is True
        assert result["failed"] is False
        assert result["failure_reason"] is None

    def test_failed_agent_returns_failure_reason(self):
        driver = InitGateDriver()
        gate = _build_gate(probe=FakeProbe([False]))
        driver.register("a1", gate)
        gate.force_fail("test_failure")
        result = driver.get_state("a1")
        assert result["state"] == "INIT_FAIL"
        assert result["failed"] is True
        assert result["ready"] is False
        assert result["failure_reason"] == "test_failure"


# ---------- tick_all ----------


class TestTickAll:
    def test_tick_all_advances_initializing_gates(self):
        driver = InitGateDriver()
        probe1 = FakeProbe([True, True, True])
        probe2 = FakeProbe([False, False, False])
        gate1 = _build_gate(probe=probe1, steady_count=2)
        gate2 = _build_gate(probe=probe2, steady_count=2)
        driver.register("a1", gate1)
        driver.register("a2", gate2)

        # Each tick_all advances both gates one step.
        for _ in range(5):
            driver.tick_all()

        # gate1 should be READY (probe always True, steady_count=2 reached)
        assert gate1.state == InitGateState.READY
        # gate2 should still be INITIALIZING (probe always False)
        assert gate2.state == InitGateState.INITIALIZING

    def test_tick_all_skips_ready_gates(self):
        """Once READY, a gate's probe should not be invoked again."""
        driver = InitGateDriver()
        probe = FakeProbe([True, True, True, True, True])
        gate = _build_gate(probe=probe, steady_count=2)
        driver.register("a1", gate)
        # Drive to READY
        while gate.state != InitGateState.READY:
            driver.tick_all()
        ready_call_count = probe.call_count
        # Further ticks should NOT call the probe again
        for _ in range(5):
            driver.tick_all()
        assert probe.call_count == ready_call_count

    def test_tick_all_skips_failed_gates(self):
        """Once INIT_FAIL, a gate's probe should not be invoked again."""
        driver = InitGateDriver()
        probe = FakeProbe([True])
        gate = _build_gate(probe=probe)
        driver.register("a1", gate)
        gate.force_fail("preset")
        for _ in range(5):
            driver.tick_all()
        assert probe.call_count == 0

    def test_tick_all_isolates_failing_gate(self):
        """A gate raising in tick() must not stop other gates from advancing."""
        driver = InitGateDriver()
        bad_gate = _build_gate(probe=RaisingProbe())
        good_probe = FakeProbe([True, True, True])
        good_gate = _build_gate(probe=good_probe, steady_count=2)
        driver.register("bad", bad_gate)
        driver.register("good", good_gate)

        # tick_all should not raise even though bad_gate's probe raises.
        # However: InitGate.tick() catches probe exceptions internally,
        # so this exercises the OUTER try/except only when tick() itself
        # raises (e.g. capture_fn or other infrastructure failure).
        # To exercise the outer try/except, we monkey-patch the gate's
        # tick to raise.
        def boom() -> InitGateState:
            raise RuntimeError("tick failed")

        bad_gate.tick = boom  # type: ignore[method-assign]

        for _ in range(5):
            driver.tick_all()

        # bad_gate forced into INIT_FAIL by driver
        assert bad_gate.state == InitGateState.INIT_FAIL
        assert bad_gate.last_reason.startswith("tick_exception:")
        # good_gate progressed normally
        assert good_gate.state == InitGateState.READY


# ---------- is_ready ----------


class TestIsReady:
    def test_unknown_agent_is_not_ready(self):
        driver = InitGateDriver()
        assert driver.is_ready("ghost") is False

    def test_initializing_agent_is_not_ready(self):
        driver = InitGateDriver()
        gate = _build_gate(probe=FakeProbe([False]))
        driver.register("a1", gate)
        gate.tick()
        assert driver.is_ready("a1") is False

    def test_ready_agent_is_ready(self):
        driver = InitGateDriver()
        gate = _build_gate(probe=FakeProbe([True, True, True]), steady_count=2)
        driver.register("a1", gate)
        while gate.state != InitGateState.READY:
            gate.tick()
        assert driver.is_ready("a1") is True

    def test_failed_agent_is_not_ready(self):
        driver = InitGateDriver()
        gate = _build_gate(probe=FakeProbe([True]))
        driver.register("a1", gate)
        gate.force_fail("test")
        assert driver.is_ready("a1") is False


# ---------- registered_count ----------


class TestRegisteredCount:
    def test_empty_driver_count_zero(self):
        driver = InitGateDriver()
        assert driver.registered_count() == 0

    def test_count_grows_with_registrations(self):
        driver = InitGateDriver()
        for n, name in enumerate(["a1", "a2", "a3"], start=1):
            driver.register(name, _build_gate(probe=FakeProbe([True])))
            assert driver.registered_count() == n

    def test_count_unaffected_by_state_transition(self):
        """READY/INIT_FAIL gates remain in registry; count doesn't drop."""
        driver = InitGateDriver()
        gate = _build_gate(probe=FakeProbe([True, True, True]), steady_count=2)
        driver.register("a1", gate)
        while gate.state != InitGateState.READY:
            gate.tick()
        assert driver.registered_count() == 1

    def test_count_decreases_on_unregister(self):
        driver = InitGateDriver()
        driver.register("a1", _build_gate(probe=FakeProbe([True])))
        driver.register("a2", _build_gate(probe=FakeProbe([True])))
        driver.unregister("a1")
        assert driver.registered_count() == 1
