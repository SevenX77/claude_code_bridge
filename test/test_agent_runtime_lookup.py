from __future__ import annotations

import json
from pathlib import Path

from agents.runtime_lookup import find_agent_runtime_by_provider


def _write_runtime(work_dir: Path, agent_name: str, payload: dict) -> Path:
    agent_dir = work_dir / ".ccb" / "agents" / agent_name
    agent_dir.mkdir(parents=True, exist_ok=True)
    path = agent_dir / "runtime.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _runtime(*, agent_name: str, provider: str, pane_id: str, last_seen_at: str, pane_state: str = "alive") -> dict:
    return {
        "schema_version": 2,
        "record_type": "agent_runtime",
        "agent_name": agent_name,
        "provider": provider,
        "pane_id": pane_id,
        "active_pane_id": pane_id,
        "pane_state": pane_state,
        "terminal_backend": "tmux",
        "tmux_socket_path": "/tmp/fake-ccbd.sock",
        "tmux_socket_name": None,
        "last_seen_at": last_seen_at,
        "workspace_path": "/fake/work",
    }


def test_returns_runtime_for_matching_provider(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CCB_PROJECT_DIR", raising=False)
    work_dir = tmp_path / "proj"
    work_dir.mkdir()
    (work_dir / ".ccb").mkdir()
    _write_runtime(
        work_dir,
        "a2",
        _runtime(agent_name="a2", provider="gemini", pane_id="%3", last_seen_at="2026-04-26T20:00:00Z"),
    )

    record = find_agent_runtime_by_provider(work_dir, "gemini")

    assert record is not None
    assert record["agent_name"] == "a2"
    assert record["pane_id"] == "%3"
    assert record["tmux_socket_path"] == "/tmp/fake-ccbd.sock"


def test_returns_none_when_no_agent_matches_provider(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CCB_PROJECT_DIR", raising=False)
    work_dir = tmp_path / "proj"
    work_dir.mkdir()
    (work_dir / ".ccb").mkdir()
    _write_runtime(
        work_dir,
        "a1",
        _runtime(agent_name="a1", provider="codex", pane_id="%6", last_seen_at="2026-04-26T20:00:00Z"),
    )

    assert find_agent_runtime_by_provider(work_dir, "gemini") is None


def test_returns_none_when_no_project_anchor(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CCB_PROJECT_DIR", raising=False)
    work_dir = tmp_path / "no-anchor"
    work_dir.mkdir()

    assert find_agent_runtime_by_provider(work_dir, "gemini") is None


def test_prefers_most_recently_active_runtime_when_multiple_match(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CCB_PROJECT_DIR", raising=False)
    work_dir = tmp_path / "proj"
    work_dir.mkdir()
    (work_dir / ".ccb").mkdir()
    _write_runtime(
        work_dir,
        "a2",
        _runtime(agent_name="a2", provider="gemini", pane_id="%3", last_seen_at="2026-04-26T19:00:00Z"),
    )
    _write_runtime(
        work_dir,
        "a4",
        _runtime(agent_name="a4", provider="gemini", pane_id="%9", last_seen_at="2026-04-26T20:30:00Z"),
    )

    record = find_agent_runtime_by_provider(work_dir, "gemini")

    assert record is not None
    assert record["agent_name"] == "a4"
    assert record["pane_id"] == "%9"


def test_skips_dead_pane_runtimes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CCB_PROJECT_DIR", raising=False)
    work_dir = tmp_path / "proj"
    work_dir.mkdir()
    (work_dir / ".ccb").mkdir()
    _write_runtime(
        work_dir,
        "a2",
        _runtime(
            agent_name="a2",
            provider="gemini",
            pane_id="%3",
            last_seen_at="2026-04-26T20:30:00Z",
            pane_state="dead",
        ),
    )
    _write_runtime(
        work_dir,
        "a4",
        _runtime(
            agent_name="a4",
            provider="gemini",
            pane_id="%9",
            last_seen_at="2026-04-26T20:00:00Z",
            pane_state="alive",
        ),
    )

    record = find_agent_runtime_by_provider(work_dir, "gemini")

    assert record is not None
    assert record["agent_name"] == "a4"


def test_walks_upward_from_subdirectory_to_find_anchor(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CCB_PROJECT_DIR", raising=False)
    work_dir = tmp_path / "proj"
    work_dir.mkdir()
    (work_dir / ".ccb").mkdir()
    _write_runtime(
        work_dir,
        "a2",
        _runtime(agent_name="a2", provider="gemini", pane_id="%3", last_seen_at="2026-04-26T20:00:00Z"),
    )
    nested = work_dir / "src" / "deep"
    nested.mkdir(parents=True)

    record = find_agent_runtime_by_provider(nested, "gemini")

    assert record is not None
    assert record["agent_name"] == "a2"


def test_skips_malformed_runtime_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CCB_PROJECT_DIR", raising=False)
    work_dir = tmp_path / "proj"
    work_dir.mkdir()
    (work_dir / ".ccb").mkdir()
    bad_dir = work_dir / ".ccb" / "agents" / "broken"
    bad_dir.mkdir(parents=True)
    (bad_dir / "runtime.json").write_text("not valid json {{", encoding="utf-8")
    _write_runtime(
        work_dir,
        "a2",
        _runtime(agent_name="a2", provider="gemini", pane_id="%3", last_seen_at="2026-04-26T20:00:00Z"),
    )

    record = find_agent_runtime_by_provider(work_dir, "gemini")

    assert record is not None
    assert record["agent_name"] == "a2"
