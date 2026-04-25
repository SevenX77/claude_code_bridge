"""RPC handler for ``init_state`` (Q3 Stage 1b Step 3).

Routes ``ccb init_state <agent_name>`` style queries to
``InitGateDriver.get_state(agent_name)``. Used by tmux_send.py (Step 4)
to fast-path skip waiting on agents that are already READY, and to fail
fast on agents that are INIT_FAIL instead of blindly retrying.
"""
from __future__ import annotations


def build_init_state_handler(driver):
    """Return a handler callable bound to the given InitGateDriver.

    The handler accepts a payload with key ``agent_name`` and returns
    the dict produced by ``driver.get_state(agent_name)`` (see schema
    in ``provider_core.init_gate_driver.InitGateDriver.get_state``).
    """

    def handle(payload: dict) -> dict:
        agent_name = str(payload.get('agent_name') or '').strip()
        if not agent_name:
            raise ValueError('init_state requires agent_name')
        return driver.get_state(agent_name)

    return handle
