from __future__ import annotations

from dataclasses import replace

from completion.models import CompletionConfidence, CompletionDecision, CompletionItemKind, CompletionStatus

from .base import ProviderPollResult, ProviderSubmission
from .common import build_item, request_anchor_from_runtime_state


def complete_no_wrap_after_prompt_sent(submission: ProviderSubmission, *, now: str) -> ProviderPollResult | None:
    if not bool(submission.runtime_state.get('no_wrap', False)):
        return None
    if not bool(submission.runtime_state.get('prompt_sent', False)):
        return None
    if bool(submission.runtime_state.get('no_wrap_terminal_emitted', False)):
        return None

    request_anchor = request_anchor_from_runtime_state(submission.runtime_state, fallback=submission.job_id)
    next_seq = int(submission.runtime_state.get('next_seq', 1))
    provider_turn_ref = request_anchor or submission.job_id
    item = build_item(
        submission,
        kind=CompletionItemKind.ASSISTANT_FINAL,
        timestamp=now,
        seq=next_seq,
        payload={
            'reply': '',
            'text': '',
            'turn_id': request_anchor or None,
            'provider_turn_ref': provider_turn_ref,
            'completion_source': 'no_wrap_dispatch',
            'status': CompletionStatus.COMPLETED.value,
        },
        cursor_kwargs={'opaque_cursor': f'no_wrap:{provider_turn_ref}'},
    )
    decision = CompletionDecision(
        terminal=True,
        status=CompletionStatus.COMPLETED,
        reason='no_wrap_prompt_sent',
        confidence=CompletionConfidence.OBSERVED,
        reply='',
        anchor_seen=True,
        reply_started=False,
        reply_stable=True,
        provider_turn_ref=provider_turn_ref,
        source_cursor=item.cursor,
        finished_at=now,
        diagnostics={
            'completion_source': 'no_wrap_dispatch',
            'provider': submission.provider,
            'submission_mode': submission.runtime_state.get('mode') or 'active',
        },
    )
    updated = replace(
        submission,
        reply='',
        runtime_state={
            **submission.runtime_state,
            'next_seq': next_seq + 1,
            'no_wrap_terminal_emitted': True,
        },
    )
    return ProviderPollResult(submission=updated, items=(item,), decision=decision)


__all__ = ['complete_no_wrap_after_prompt_sent']
