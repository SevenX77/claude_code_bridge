"""Unit tests for init_gate_registration factory (Q3 Stage 1b Step 4)."""
from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from ccbd.services.init_gate_registration import (
    register_provider_init_gate_for_agent,
    register_provider_init_gates_for_started,
)
from provider_core.init_gate_driver import InitGateDriver


class _FakePaths:
    def __init__(self, base: Path) -> None:
        self.base = base

    def agent_provider_runtime_dir(self, agent_name: str, provider: str) -> Path:
        return self.base / agent_name / 'provider-runtime' / provider


class _FakeRegistry:
    def __init__(self) -> None:
        self._runtimes: dict[str, SimpleNamespace] = {}

    def add(self, runtime: SimpleNamespace) -> None:
        self._runtimes[runtime.agent_name] = runtime

    def get(self, agent_name: str):
        return self._runtimes.get(agent_name)


def _build_app(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        registry=_FakeRegistry(),
        init_gate_driver=InitGateDriver(),
        paths=_FakePaths(tmp_path),
    )


def _runtime(agent_name: str, *, provider: str | None,
             pane_id: str | None = "%4",
             tmux_socket_path: str | None = None,
             tmux_socket_name: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        agent_name=agent_name,
        provider=provider,
        pane_id=pane_id,
        tmux_socket_path=tmux_socket_path,
        tmux_socket_name=tmux_socket_name,
    )


class TestRegisterProviderInitGateForAgent:
    def test_gemini_registers_and_creates_runtime_dir(self, tmp_path):
        app = _build_app(tmp_path)
        app.registry.add(_runtime('a1', provider='gemini'))
        result = register_provider_init_gate_for_agent(app, 'a1')
        assert result is True
        assert app.init_gate_driver.registered_count() == 1
        # runtime_dir should have been created
        expected = tmp_path / 'a1' / 'provider-runtime' / 'gemini'
        assert expected.is_dir()

    def test_codex_is_no_op(self, tmp_path):
        app = _build_app(tmp_path)
        app.registry.add(_runtime('a1', provider='codex'))
        result = register_provider_init_gate_for_agent(app, 'a1')
        assert result is False
        assert app.init_gate_driver.registered_count() == 0

    def test_claude_is_no_op_pending_stage_1c(self, tmp_path):
        app = _build_app(tmp_path)
        app.registry.add(_runtime('a1', provider='claude'))
        result = register_provider_init_gate_for_agent(app, 'a1')
        assert result is False
        assert app.init_gate_driver.registered_count() == 0

    def test_unknown_provider_logs_warning_and_skips(self, tmp_path, caplog):
        app = _build_app(tmp_path)
        app.registry.add(_runtime('a1', provider='gpt5'))
        with caplog.at_level(logging.WARNING):
            result = register_provider_init_gate_for_agent(app, 'a1')
        assert result is False
        assert app.init_gate_driver.registered_count() == 0
        assert any('skip_unknown_provider' in r.getMessage() for r in caplog.records)

    def test_missing_runtime_returns_false(self, tmp_path):
        app = _build_app(tmp_path)
        # registry is empty
        result = register_provider_init_gate_for_agent(app, 'ghost')
        assert result is False

    def test_missing_pane_id_skips(self, tmp_path):
        app = _build_app(tmp_path)
        app.registry.add(_runtime('a1', provider='gemini', pane_id=None))
        result = register_provider_init_gate_for_agent(app, 'a1')
        assert result is False
        assert app.init_gate_driver.registered_count() == 0

    def test_lookup_failure_returns_false_without_raising(self, tmp_path):
        class _BadRegistry:
            def get(self, agent_name):
                raise RuntimeError("registry exploded")
        app = SimpleNamespace(
            registry=_BadRegistry(),
            init_gate_driver=InitGateDriver(),
            paths=_FakePaths(tmp_path),
        )
        # must not raise
        result = register_provider_init_gate_for_agent(app, 'a1')
        assert result is False


class TestRegisterProviderInitGatesForStarted:
    def test_bulk_registers_only_supported(self, tmp_path):
        app = _build_app(tmp_path)
        app.registry.add(_runtime('gem1', provider='gemini'))
        app.registry.add(_runtime('cod1', provider='codex'))
        app.registry.add(_runtime('gem2', provider='gemini'))
        registered = register_provider_init_gates_for_started(
            app, ['gem1', 'cod1', 'gem2']
        )
        assert set(registered) == {'gem1', 'gem2'}
        assert app.init_gate_driver.registered_count() == 2

    def test_single_failure_does_not_abort_loop(self, tmp_path):
        app = _build_app(tmp_path)
        # gem1 has no runtime → skipped silently
        app.registry.add(_runtime('gem2', provider='gemini'))
        registered = register_provider_init_gates_for_started(
            app, ['gem1', 'gem2']
        )
        assert registered == ('gem2',)
