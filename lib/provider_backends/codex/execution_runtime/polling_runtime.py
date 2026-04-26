from __future__ import annotations

from dataclasses import replace

from provider_execution.active import prepare_active_poll
from provider_execution.base import ProviderPollResult, ProviderSubmission
from provider_execution.no_wrap_terminal import complete_no_wrap_after_prompt_sent
from provider_execution.pane_stability_terminal import observe_pane_stability, terminal_if_stable

from .event_reading import read_entries
from .start import state_session_path
from .state_machine import (
    apply_session_rotation,
    build_poll_state,
    finalize_poll_result,
    handle_assistant_entry,
    handle_terminal_entry,
    handle_user_entry,
    update_binding_refs,
)


def poll_submission(submission: ProviderSubmission, *, now: str) -> ProviderPollResult | None:
    prepared = prepare_active_poll(submission, now=now)
    if prepared is None or isinstance(prepared, ProviderPollResult):
        return prepared

    state = submission.runtime_state.get("state") or {}
    poll = build_poll_state(submission)
    state = poll_entry_batches(submission, poll, prepared.reader, state, now=now)
    if not poll.reached_terminal:
        no_wrap_terminal = complete_no_wrap_after_prompt_sent(submission, now=now)
        if no_wrap_terminal is not None:
            return no_wrap_terminal
        fallback_submission = replace_state_for_fallback(submission, poll, state)
        fallback_submission = observe_pane_stability(
            fallback_submission,
            now=now,
            get_pane_content_fn=getattr(prepared.backend, "get_pane_content", None),
            pane_id=prepared.pane_id,
            log_path_str=str(fallback_submission.runtime_state.get("session_path") or ""),
        )
        pane_stable_terminal = terminal_if_stable(
            fallback_submission,
            now=now,
            require_log_mtime=True,
        )
        if pane_stable_terminal is not None:
            return pane_stable_terminal
        submission = fallback_submission
    return finalize_poll_result(submission, poll, state=state)


def replace_state_for_fallback(submission, poll, state):
    return replace(
        submission,
        runtime_state={
            **submission.runtime_state,
            "state": state,
            "session_path": poll.session_path,
            "next_seq": poll.next_seq,
        },
    )


def poll_entry_batches(submission, poll, reader, state, *, now: str):
    current_state = state
    while True:
        entries, current_state = read_entries(reader, current_state)
        apply_session_state(submission, poll, current_state, now=now)
        if not entries:
            break
        process_entry_batch(submission, poll, entries, now=now)
        if poll.reached_terminal:
            break
    return current_state


def apply_session_state(submission, poll, state, *, now: str) -> None:
    apply_session_rotation(
        submission,
        poll,
        new_session_path=state_session_path(state),
        now=now,
    )


def process_entry_batch(submission, poll, entries, *, now: str) -> None:
    for entry in entries:
        process_entry(submission, poll, entry, now=now)
        if poll.reached_terminal:
            break


def process_entry(submission, poll, entry, *, now: str) -> None:
    update_binding_refs(poll, entry)
    role = str(entry.get("role") or "").strip().lower()
    if role == "user":
        handle_user_entry(submission, poll, text=str(entry.get("text") or ""), now=now)
        return
    if not poll.anchor_seen:
        return
    if role == "assistant":
        handle_assistant_entry(submission, poll, entry, now=now)
        return
    handle_terminal_entry(submission, poll, entry, now=now)


__all__ = ["poll_submission"]
