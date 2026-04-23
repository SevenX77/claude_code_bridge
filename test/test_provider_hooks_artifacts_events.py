from __future__ import annotations

import json
from pathlib import Path

import pytest

from provider_hooks.artifacts_runtime.events import reception_path, write_reception


def test_write_reception_creates_file_with_expected_fields(tmp_path: Path) -> None:
    reception_dir = tmp_path / "reception"
    path = write_reception(
        reception_dir=reception_dir,
        agent_name="a2",
        workspace_path="/home/sevenx",
        req_id="job_abc123",
        session_id="sess-1",
        hook_event_name="BeforeAgent",
        prompt_preview="hello world",
    )
    assert path == reception_dir / "events" / "job_abc123.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["req_id"] == "job_abc123"
    assert data["agent_name"] == "a2"
    assert data["session_id"] == "sess-1"
    assert data["hook_event_name"] == "BeforeAgent"
    assert data["prompt_preview"] == "hello world"
    assert "timestamp_iso" in data


def test_reception_path_returns_events_subdir(tmp_path: Path) -> None:
    assert reception_path(tmp_path, "job_xyz") == tmp_path / "events" / "job_xyz.json"
