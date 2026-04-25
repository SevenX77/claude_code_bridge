"""Integration tests for Q3 Stage 1 Init Gate end-to-end behavior.

Covers the three providers' init-gate paths against a live ccbd:
    - Codex (Stage 1a, commit 8340d6e): per-bridge gate; init_state RPC
      reports `registered=False` for codex agents (gate lives in bridge).
    - Gemini (Stage 1b, commits 6373f19/801edd3/a6ddc7b/7adb7bd): ccbd
      driver-managed gate; init_state RPC returns full state schema.
    - Claude (Stage 1c, commit e6a5a05): same as Gemini.

Each test SKIPs (does NOT fail) when the environment is missing — these
are smoke checks intended to run on a host with `ccb ps` reporting the
agents mounted. The CI matrix should run them only on the integration
job.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from pathlib import Path

import pytest


def _project_workspace() -> Path:
    """Resolve workspace by walking up to find .ccb/ccbd/ccbd.sock.

    Honors CCB_TEST_WORKSPACE override (validates the socket exists
    underneath). Otherwise walks up from this test file, then from
    cwd, then $HOME.
    """
    raw = os.environ.get("CCB_TEST_WORKSPACE", "").strip()
    candidates: list[Path] = []
    if raw:
        candidates.append(Path(raw))
    # Walk-up search starting points (most specific first).
    for start in (Path(__file__).resolve().parent.parent, Path.cwd(), Path.home()):
        for ancestor in [start, *start.parents]:
            if (ancestor / ".ccb" / "ccbd" / "ccbd.sock").exists():
                candidates.append(ancestor)
                break
    seen: set[Path] = set()
    for c in candidates:
        c = c.resolve()
        if c in seen:
            continue
        seen.add(c)
        if (c / ".ccb" / "ccbd" / "ccbd.sock").exists():
            return c
    pytest.skip("no live ccbd socket found in any walk-up candidate")


def _ccbd_socket_path(workspace: Path) -> Path | None:
    """Find ccbd socket; SKIP-friendly None if missing."""
    sock = workspace / ".ccb" / "ccbd" / "ccbd.sock"
    return sock if sock.exists() else None


def _build_client(workspace: Path):
    """Construct CcbdClient or SKIP if anything is missing."""
    sock = _ccbd_socket_path(workspace)
    if sock is None:
        pytest.skip(f"ccbd not mounted at {workspace}/.ccb/ccbd/ccbd.sock")
    from ccbd.socket_client import CcbdClient
    return CcbdClient(sock, timeout_s=3.0)


def _agent_provider_runtime_dir(workspace: Path, agent: str, provider: str) -> Path:
    return workspace / ".ccb" / "agents" / agent / "provider-runtime" / provider


# ---------- init_state RPC ----------


@pytest.mark.integration
class TestInitStateRpc:
    """init_state RPC schema and provider-specific behavior."""

    def test_unknown_agent_returns_not_registered(self):
        workspace = _project_workspace()
        client = _build_client(workspace)
        out = client.init_state("ghost-never-existed")
        assert out["agent_name"] == "ghost-never-existed"
        assert out["registered"] is False
        assert out["state"] is None
        assert out["ready"] is False
        assert out["failed"] is False
        assert out["failure_reason"] is None

    def test_codex_agent_is_not_in_driver_registry(self):
        """Codex's gate lives in the bridge process; ccbd driver doesn't track it."""
        workspace = _project_workspace()
        client = _build_client(workspace)
        # Use a1 if it exists, else SKIP
        try:
            out = client.init_state("a1")
        except Exception as exc:
            pytest.skip(f"could not query a1: {exc}")
        # Codex may or may not be present. If registered, that means
        # someone added codex to _GATE_BUILDERS — flag that.
        if out["registered"]:
            pytest.fail(
                "a1 (codex) is registered in InitGateDriver — Stage 1a "
                "design says codex owns its gate inside the bridge "
                "process. Did _GATE_BUILDERS gain a 'codex' entry?"
            )
        assert out["state"] is None
        assert out["ready"] is False

    def test_gemini_agent_has_registered_gate(self):
        """Gemini Stage 1b: gate is registered in driver after mount."""
        workspace = _project_workspace()
        client = _build_client(workspace)
        try:
            out = client.init_state("a2")
        except Exception as exc:
            pytest.skip(f"could not query a2: {exc}")
        if not out["registered"]:
            pytest.skip(
                "a2 has no registered gate — agent may have been mounted "
                "before Stage 1b shipped, or a2 is not gemini."
            )
        assert out["state"] in ("LAUNCHED", "INITIALIZING", "READY", "INIT_FAIL")
        # No matter the state, the response shape must be stable.
        assert isinstance(out["ready"], bool)
        assert isinstance(out["failed"], bool)
        if out["failed"]:
            assert isinstance(out["failure_reason"], str) and out["failure_reason"]
        else:
            assert out["failure_reason"] is None

    def test_claude_agent_has_registered_gate(self):
        """Claude Stage 1c: gate is registered in driver after mount."""
        workspace = _project_workspace()
        client = _build_client(workspace)
        try:
            out = client.init_state("a3")
        except Exception as exc:
            pytest.skip(f"could not query a3: {exc}")
        if not out["registered"]:
            pytest.skip(
                "a3 has no registered gate — agent may have been mounted "
                "before Stage 1c shipped, or a3 is not claude."
            )
        assert out["state"] in ("LAUNCHED", "INITIALIZING", "READY", "INIT_FAIL")
        assert isinstance(out["ready"], bool)
        assert isinstance(out["failed"], bool)


# ---------- init_gate_failure.json schema ----------


@pytest.mark.integration
class TestInitGateFailureArtifact:
    """If init_gate_failure.json exists, it must conform to the documented schema."""

    @pytest.mark.parametrize("agent,provider", [
        ("a1", "codex"),
        ("a2", "gemini"),
        ("a3", "claude"),
    ])
    def test_failure_json_schema_when_present(self, agent, provider):
        workspace = _project_workspace()
        runtime_dir = _agent_provider_runtime_dir(workspace, agent, provider)
        failure_path = runtime_dir / "init_gate_failure.json"
        if not failure_path.exists():
            pytest.skip(f"no failure artifact at {failure_path} (gate succeeded)")
        data = json.loads(failure_path.read_text())
        # Required fields per InitGate._record_failure
        assert data["provider"] == provider
        assert isinstance(data["reason"], str) and data["reason"]
        assert isinstance(data["deadline_s"], (int, float))
        assert isinstance(data["elapsed_s"], (int, float))
        assert "init_gate_bypass" in data
        assert isinstance(data["recent_pane_captures"], list)
        assert isinstance(data["probes_attempted"], list)
        assert isinstance(data["timestamp"], str) and data["timestamp"]


# ---------- end-to-end send-after-mount ----------


@pytest.mark.integration
class TestSendAfterMountSucceeds:
    """A `ccb ask` to a mounted agent should succeed without gate-induced hang."""

    @pytest.mark.parametrize("agent", ["a1", "a2", "a3"])
    def test_ask_returns_within_timeout(self, agent):
        workspace = _project_workspace()
        if not (workspace / ".ccb" / "ccbd").exists():
            pytest.skip(f"ccbd not mounted at {workspace}")
        unique = f"ig-e2e-{uuid.uuid4().hex[:8]}"
        # Use ccb directly. If agent is unhealthy, command exits non-zero
        # quickly — that's still a pass for "no infinite hang from gate".
        try:
            proc = subprocess.run(
                ["ccb", "ask", "--wait", "--timeout", "60", agent, f"reply '{unique}'"],
                cwd=str(workspace),
                capture_output=True,
                text=True,
                timeout=90,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            pytest.fail(
                f"ccb ask to {agent} hung past 90s — possible init gate "
                "deadlock (regression in fall-through semantics?)"
            )
        # Either successful (rc=0) or graceful failure — never hang.
        # Surface stderr for debug if rc != 0; do not assert success
        # because the agent may be in a degraded state independent of
        # the gate path.
        if proc.returncode != 0:
            pytest.skip(
                f"ccb ask to {agent} exited rc={proc.returncode}; this "
                f"test only checks 'no hang', not 'agent healthy'. "
                f"stderr tail: {proc.stderr[-300:]}"
            )
        assert proc.stdout, "expected some stdout from successful ask"
