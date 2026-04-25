"""Unit tests for the init_state RPC handler (Q3 Stage 1b Step 3)."""
from __future__ import annotations

import pytest

from ccbd.handlers import build_init_state_handler
from provider_core.init_gate_driver import InitGateDriver


class _FakeDriver:
    """Stub driver: records the agent_name passed in and returns a stored shape."""

    def __init__(self, response: dict) -> None:
        self.response = response
        self.last_agent_name: str | None = None

    def get_state(self, agent_name: str) -> dict:
        self.last_agent_name = agent_name
        return self.response


def test_handler_routes_agent_name_to_driver():
    driver = _FakeDriver(
        response={
            "agent_name": "a1",
            "registered": True,
            "state": "READY",
            "ready": True,
            "failed": False,
            "failure_reason": None,
        }
    )
    handler = build_init_state_handler(driver)
    out = handler({"agent_name": "a1"})
    assert driver.last_agent_name == "a1"
    assert out["state"] == "READY"
    assert out["ready"] is True


def test_handler_strips_whitespace():
    driver = _FakeDriver(response={"agent_name": "a1", "registered": False, "state": None,
                                    "ready": False, "failed": False, "failure_reason": None})
    handler = build_init_state_handler(driver)
    handler({"agent_name": "  a1  "})
    assert driver.last_agent_name == "a1"


def test_handler_rejects_missing_agent_name():
    driver = _FakeDriver(response={})
    handler = build_init_state_handler(driver)
    with pytest.raises(ValueError, match="requires agent_name"):
        handler({})


def test_handler_rejects_empty_agent_name():
    driver = _FakeDriver(response={})
    handler = build_init_state_handler(driver)
    with pytest.raises(ValueError, match="requires agent_name"):
        handler({"agent_name": ""})


def test_handler_works_with_real_driver_unknown_agent():
    """Smoke test against the real InitGateDriver returning not_registered."""
    driver = InitGateDriver()
    handler = build_init_state_handler(driver)
    out = handler({"agent_name": "ghost"})
    assert out == {
        "agent_name": "ghost",
        "registered": False,
        "state": None,
        "ready": False,
        "failed": False,
        "failure_reason": None,
    }
