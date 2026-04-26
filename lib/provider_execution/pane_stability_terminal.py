from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

from ccbd.system import parse_utc_timestamp
from completion.models import CompletionConfidence, CompletionDecision, CompletionItemKind, CompletionStatus

from .base import ProviderPollResult, ProviderSubmission
from .common import build_item, request_anchor_from_runtime_state


PANE_STABLE_TERMINAL_FLAG = "pane_stable_terminal_emitted"
LOG_IDLE_THRESHOLD_S = 20.0
PANE_STABLE_THRESHOLD_S = 5.0
PANE_ONLY_THRESHOLD_S = 15.0


def complete_after_pane_idle(
    submission: ProviderSubmission,
    *,
    now: str,
    get_pane_content_fn,
    pane_id: str,
    log_path_str: str | None,
    pane_only_threshold_s: float = PANE_ONLY_THRESHOLD_S,
    require_log_mtime: bool = False,
) -> ProviderPollResult | None:
    observed = observe_pane_stability(
        submission,
        now=now,
        get_pane_content_fn=get_pane_content_fn,
        pane_id=pane_id,
        log_path_str=log_path_str,
    )
    return terminal_if_stable(
        observed,
        now=now,
        pane_only_threshold_s=pane_only_threshold_s,
        require_log_mtime=require_log_mtime,
    )


def observe_pane_stability(
    submission: ProviderSubmission,
    *,
    now: str,
    get_pane_content_fn,
    pane_id: str,
    log_path_str: str | None,
) -> ProviderSubmission:
    if bool(submission.runtime_state.get(PANE_STABLE_TERMINAL_FLAG, False)):
        return submission
    if not callable(get_pane_content_fn):
        return submission

    try:
        text = str(get_pane_content_fn(pane_id, lines=200) or "")
    except Exception:
        return submission

    current_hash = hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()
    last_hash = str(submission.runtime_state.get("pane_hash_last") or "")

    if current_hash != last_hash:
        return replace(
            submission,
            runtime_state={
                **submission.runtime_state,
                "pane_hash_last": current_hash,
                "pane_hash_seen_at": now,
            },
        )

    current_log_mtime = _log_mtime_ns(log_path_str)
    if current_log_mtime is None:
        return submission

    log_mtime_last = str(submission.runtime_state.get("log_mtime_last") or "")
    if current_log_mtime != log_mtime_last:
        return replace(
            submission,
            runtime_state={
                **submission.runtime_state,
                "log_mtime_last": current_log_mtime,
                "log_mtime_seen_at": now,
            },
        )
    return submission


def terminal_if_stable(
    submission: ProviderSubmission,
    *,
    now: str,
    pane_only_threshold_s: float = PANE_ONLY_THRESHOLD_S,
    require_log_mtime: bool = False,
) -> ProviderPollResult | None:
    if bool(submission.runtime_state.get(PANE_STABLE_TERMINAL_FLAG, False)):
        return None

    pane_hash_seen_at = str(submission.runtime_state.get("pane_hash_seen_at") or "")
    log_mtime_last = str(submission.runtime_state.get("log_mtime_last") or "")
    log_mtime_seen_at = str(submission.runtime_state.get("log_mtime_seen_at") or "")
    if require_log_mtime and (not log_mtime_last or not log_mtime_seen_at):
        return None
    if not log_mtime_last or not log_mtime_seen_at:
        return _complete_if_pane_only_threshold_met(
            submission,
            now=now,
            pane_hash_seen_at=pane_hash_seen_at,
            pane_only_threshold_s=pane_only_threshold_s,
        )
    try:
        now_dt = parse_utc_timestamp(now)
        pane_stable_s = (now_dt - parse_utc_timestamp(pane_hash_seen_at)).total_seconds() if pane_hash_seen_at else 0.0
        log_idle_s = (now_dt - parse_utc_timestamp(log_mtime_seen_at)).total_seconds() if log_mtime_seen_at else 0.0
    except Exception:
        return None

    if pane_stable_s < PANE_STABLE_THRESHOLD_S or log_idle_s < LOG_IDLE_THRESHOLD_S:
        return None
    return _terminal_result(submission, now=now, completion_source="pane_stable_fallback")


def _complete_if_pane_only_threshold_met(
    submission: ProviderSubmission,
    *,
    now: str,
    pane_hash_seen_at: str,
    pane_only_threshold_s: float,
) -> ProviderPollResult | None:
    try:
        pane_stable_s = (parse_utc_timestamp(now) - parse_utc_timestamp(pane_hash_seen_at)).total_seconds() if pane_hash_seen_at else 0.0
    except Exception:
        return None
    if pane_stable_s < pane_only_threshold_s:
        return None
    return _terminal_result(submission, now=now, completion_source="pane_stable_fallback_pane_only")


def _log_mtime_ns(log_path_str: str | None) -> str | None:
    if not log_path_str:
        return None
    try:
        path = Path(log_path_str).expanduser()
        return str(path.stat().st_mtime_ns)
    except FileNotFoundError:
        return None
    except Exception:
        return None


def _terminal_result(submission: ProviderSubmission, *, now: str, completion_source: str) -> ProviderPollResult:
    request_anchor = request_anchor_from_runtime_state(submission.runtime_state, fallback=submission.job_id)
    provider_turn_ref = request_anchor or submission.job_id
    next_seq = int(submission.runtime_state.get("next_seq", 1))
    item = build_item(
        submission,
        kind=CompletionItemKind.ASSISTANT_FINAL,
        timestamp=now,
        seq=next_seq,
        payload={
            "reply": "",
            "text": "",
            "turn_id": request_anchor or None,
            "provider_turn_ref": provider_turn_ref,
            "completion_source": completion_source,
            "status": CompletionStatus.COMPLETED.value,
        },
        cursor_kwargs={"opaque_cursor": f"{completion_source}:{provider_turn_ref}"},
    )
    decision = CompletionDecision(
        terminal=True,
        status=CompletionStatus.COMPLETED,
        reason="pane_stable_fallback",
        confidence=CompletionConfidence.DEGRADED,
        reply="",
        anchor_seen=True,
        reply_started=False,
        reply_stable=True,
        provider_turn_ref=provider_turn_ref,
        source_cursor=item.cursor,
        finished_at=now,
        diagnostics={
            "completion_source": completion_source,
            "provider": submission.provider,
            "submission_mode": submission.runtime_state.get("mode") or "active",
        },
    )
    updated = replace(
        submission,
        reply="",
        runtime_state={
            **submission.runtime_state,
            "next_seq": next_seq + 1,
            PANE_STABLE_TERMINAL_FLAG: True,
        },
    )
    return ProviderPollResult(submission=updated, items=(item,), decision=decision)


__all__ = [
    "LOG_IDLE_THRESHOLD_S",
    "PANE_ONLY_THRESHOLD_S",
    "PANE_STABLE_TERMINAL_FLAG",
    "PANE_STABLE_THRESHOLD_S",
    "complete_after_pane_idle",
    "observe_pane_stability",
    "terminal_if_stable",
]
