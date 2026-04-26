from __future__ import annotations

import os
from pathlib import Path

from completion.models import CompletionItemKind
from completion.models import CompletionSourceKind, CompletionStatus
from provider_backends.codex.execution_runtime.polling import poll_submission as poll_codex_submission
from provider_execution.base import ProviderSubmission
from provider_execution.pane_stability_terminal import (
    PANE_STABLE_TERMINAL_FLAG,
    complete_after_pane_idle,
    observe_pane_stability,
    terminal_if_stable,
)


class Pane:
    def __init__(self, text: str = "ready") -> None:
        self.text = text

    def get(self, pane_id: str, *, lines: int) -> str:
        assert pane_id == "%1"
        assert lines == 200
        return self.text


def _submission(runtime_state: dict[str, object] | None = None) -> ProviderSubmission:
    return ProviderSubmission(
        job_id="job_1",
        agent_name="agent1",
        provider="codex",
        accepted_at="2026-04-26T00:00:00Z",
        ready_at="2026-04-26T00:00:00Z",
        source_kind=CompletionSourceKind.PROTOCOL_EVENT_STREAM,
        reply="",
        runtime_state={
            "mode": "active",
            "request_anchor": "req_1",
            "next_seq": 1,
            **(runtime_state or {}),
        },
    )


def _poll(submission: ProviderSubmission, pane: Pane, *, now: str, log_path: Path | None):
    observed = _observe(submission, pane, now=now, log_path=log_path)
    terminal = terminal_if_stable(observed, now=now)
    if terminal is not None:
        return terminal
    if observed != submission:
        from provider_execution.base import ProviderPollResult

        return ProviderPollResult(submission=observed)
    return None


def _observe(submission: ProviderSubmission, pane: Pane, *, now: str, log_path: Path | None):
    return observe_pane_stability(
        submission,
        now=now,
        get_pane_content_fn=pane.get,
        pane_id="%1",
        log_path_str=str(log_path) if log_path else None,
    )


def test_pane_changing_does_not_emit_terminal(tmp_path: Path) -> None:
    log_path = tmp_path / "session.jsonl"
    log_path.write_text("one\n", encoding="utf-8")
    pane = Pane("first")

    first = _poll(_submission(), pane, now="2026-04-26T00:00:00Z", log_path=log_path)
    assert first is not None
    assert first.decision is None

    pane.text = "second"
    second = _poll(first.submission, pane, now="2026-04-26T00:00:30Z", log_path=log_path)
    assert second is not None
    assert second.decision is None
    assert second.submission.runtime_state["pane_hash_seen_at"] == "2026-04-26T00:00:30Z"


def test_pane_stable_less_than_threshold_does_not_emit(tmp_path: Path) -> None:
    log_path = tmp_path / "session.jsonl"
    log_path.write_text("one\n", encoding="utf-8")
    pane = Pane()
    first = _poll(_submission(), pane, now="2026-04-26T00:00:00Z", log_path=log_path)

    result = _poll(first.submission, pane, now="2026-04-26T00:00:04Z", log_path=log_path)

    assert result is not None
    assert result.decision is None
    assert result.submission.runtime_state["log_mtime_seen_at"] == "2026-04-26T00:00:04Z"


def test_pane_stable_but_log_mtime_less_than_threshold_does_not_emit(tmp_path: Path) -> None:
    log_path = tmp_path / "session.jsonl"
    log_path.write_text("one\n", encoding="utf-8")
    pane = Pane()
    first = _poll(_submission(), pane, now="2026-04-26T00:00:00Z", log_path=log_path)
    second = _poll(first.submission, pane, now="2026-04-26T00:00:05Z", log_path=log_path)

    result = _poll(second.submission, pane, now="2026-04-26T00:00:24Z", log_path=log_path)

    assert result is None


def test_pane_and_log_stable_past_threshold_emits_terminal_completed(tmp_path: Path) -> None:
    log_path = tmp_path / "session.jsonl"
    log_path.write_text("one\n", encoding="utf-8")
    pane = Pane()
    first = _poll(_submission(), pane, now="2026-04-26T00:00:00Z", log_path=log_path)
    second = _poll(first.submission, pane, now="2026-04-26T00:00:05Z", log_path=log_path)

    result = _poll(second.submission, pane, now="2026-04-26T00:00:25Z", log_path=log_path)

    assert result is not None
    assert result.decision is not None
    assert result.decision.status is CompletionStatus.COMPLETED
    assert result.decision.reason == "pane_stable_fallback"
    assert result.items[0].payload["completion_source"] == "pane_stable_fallback"
    assert result.submission.runtime_state[PANE_STABLE_TERMINAL_FLAG] is True


def test_already_emitted_is_idempotent() -> None:
    pane = Pane()
    result = _poll(
        _submission({PANE_STABLE_TERMINAL_FLAG: True}),
        pane,
        now="2026-04-26T00:00:25Z",
        log_path=None,
    )

    assert result is None


def test_get_pane_content_exception_returns_none() -> None:
    def broken(pane_id: str, *, lines: int) -> str:
        raise RuntimeError("boom")

    observed = observe_pane_stability(
        _submission(),
        now="2026-04-26T00:00:25Z",
        get_pane_content_fn=broken,
        pane_id="%1",
        log_path_str=None,
    )

    result = terminal_if_stable(observed, now="2026-04-26T00:00:25Z")
    assert result is None


def test_missing_log_uses_pane_only_threshold() -> None:
    pane = Pane()
    first = _poll(_submission(), pane, now="2026-04-26T00:00:00Z", log_path=None)

    early = _poll(first.submission, pane, now="2026-04-26T00:00:14Z", log_path=None)
    assert early is None

    result = _poll(first.submission, pane, now="2026-04-26T00:00:15Z", log_path=None)
    assert result is not None
    assert result.decision is not None
    assert result.decision.status is CompletionStatus.COMPLETED


def test_missing_log_with_require_log_mtime_does_not_use_pane_only_threshold() -> None:
    pane = Pane()
    first = _observe(_submission(), pane, now="2026-04-26T00:00:00Z", log_path=None)

    result = terminal_if_stable(first, now="2026-04-26T00:00:30Z", require_log_mtime=True)

    assert result is None


def test_log_mtime_change_resets_log_idle_tracking(tmp_path: Path) -> None:
    log_path = tmp_path / "session.jsonl"
    log_path.write_text("one\n", encoding="utf-8")
    pane = Pane()
    first = _poll(_submission(), pane, now="2026-04-26T00:00:00Z", log_path=log_path)
    second = _poll(first.submission, pane, now="2026-04-26T00:00:05Z", log_path=log_path)

    log_path.write_text("two\n", encoding="utf-8")
    os.utime(log_path, ns=(log_path.stat().st_atime_ns, log_path.stat().st_mtime_ns + 1_000_000_000))
    result = _poll(second.submission, pane, now="2026-04-26T00:00:25Z", log_path=log_path)

    assert result is not None
    assert result.decision is None
    assert result.submission.runtime_state["log_mtime_seen_at"] == "2026-04-26T00:00:25Z"


def test_codex_observe_does_not_drop_items_when_pane_hash_first_changes(tmp_path: Path) -> None:
    log_path = tmp_path / "session.jsonl"
    log_path.write_text("one\n", encoding="utf-8")

    class Backend:
        def is_alive(self, pane_id: str) -> bool:
            return pane_id == "%1"

        def get_pane_content(self, pane_id: str, *, lines: int) -> str:
            assert pane_id == "%1"
            assert lines == 200
            return "assistant finished"

    class Reader:
        def __init__(self) -> None:
            self.calls = 0

        def try_get_entries(self, state):
            self.calls += 1
            if self.calls == 1:
                return [
                    {
                        "role": "assistant",
                        "text": "hello from codex",
                        "timestamp": "2026-04-26T00:00:01Z",
                        "phase": "final_answer",
                    }
                ], {"log_path": str(log_path)}
            return [], {"log_path": str(log_path)}

    submission = _submission(
        {
            "reader": Reader(),
            "backend": Backend(),
            "pane_id": "%1",
            "state": {"log_path": str(log_path)},
            "anchor_seen": True,
            "bound_turn_id": "",
            "bound_task_id": "",
            "reply_buffer": "",
            "last_agent_message": "",
            "last_final_answer": "",
            "last_assistant_message": "",
            "last_assistant_signature": "",
            "session_path": str(log_path),
            "no_wrap": False,
            "prompt_sent": True,
        }
    )

    result = poll_codex_submission(submission, now="2026-04-26T00:00:05Z")

    assert result is not None
    assert result.decision is None
    assert [item.kind for item in result.items] == [CompletionItemKind.ASSISTANT_CHUNK]
    assert result.submission.reply == "hello from codex"
    assert result.submission.runtime_state["reply_buffer"] == "hello from codex"
    assert result.submission.runtime_state["pane_hash_seen_at"] == "2026-04-26T00:00:05Z"
