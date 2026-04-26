from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from project.discovery import find_nearest_project_anchor


def find_agent_runtime_by_provider(work_dir: Path, provider: str) -> Optional[dict[str, Any]]:
    """Return the most recently active agent runtime record matching `provider`.

    Walks up from `work_dir` to find the nearest `.ccb` project anchor (or
    honors the `CCB_PROJECT_DIR` env var via `find_nearest_project_anchor`),
    then scans `<anchor>/.ccb/agents/<name>/runtime.json` for records whose
    `provider` field matches. Skips entries with `pane_state == "dead"` and
    malformed runtime files. Returns the dict with the latest `last_seen_at`,
    or `None` when no live match exists.
    """
    qualified_provider = (provider or "").strip().lower()
    if not qualified_provider:
        return None

    anchor = find_nearest_project_anchor(Path(work_dir))
    if anchor is None:
        return None

    agents_dir = anchor / ".ccb" / "agents"
    if not agents_dir.is_dir():
        return None

    best: Optional[dict[str, Any]] = None
    best_key: str = ""
    for runtime_path in sorted(agents_dir.glob("*/runtime.json")):
        record = _load_runtime(runtime_path)
        if record is None:
            continue
        if (str(record.get("provider") or "").strip().lower()) != qualified_provider:
            continue
        if str(record.get("pane_state") or "").strip().lower() == "dead":
            continue
        last_seen = str(record.get("last_seen_at") or "")
        if last_seen >= best_key:
            best = record
            best_key = last_seen

    return best


def _load_runtime(runtime_path: Path) -> Optional[dict[str, Any]]:
    try:
        with runtime_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data


__all__ = ["find_agent_runtime_by_provider"]
