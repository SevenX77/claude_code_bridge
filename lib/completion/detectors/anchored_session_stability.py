from __future__ import annotations
import logging
from typing import Any

from completion.detectors.base import BaseCompletionDetector
from completion.models import (
    CompletionConfidence,
    CompletionCursor,
    CompletionItem,
    CompletionItemKind,
    CompletionRequestContext,
    CompletionStatus,
    first_non_empty,
    fingerprint_text,
    seconds_between,
)

logger = logging.getLogger(__name__)


class AnchoredSessionStabilityDetector(BaseCompletionDetector):
    def __init__(
        self,
        *,
        settle_window_s: float | None = None,
        is_hook_expected: bool = False,
    ) -> None:
        super().__init__()
        self._is_hook_expected = is_hook_expected
        self._settle_window_s: float = 2.0
        self._explicit_settle_window: float | None = settle_window_s
    
    def bind(self, request_ctx: CompletionRequestContext, baseline: CompletionCursor) -> None:
        super().bind(request_ctx, baseline)
        # Compute settle_window_s after bind (when _request_ctx is available)
        if self._explicit_settle_window is not None:
            self._settle_window_s = self._explicit_settle_window
        elif self._is_hook_expected:
            self._settle_window_s = 30.0  # TD-008: give hook 30s priority
        else:
            self._settle_window_s = 2.0  # default for manual attach
        self._validate_hook_consistency()
    
    def _validate_hook_consistency(self) -> None:
        """Validate is_hook_expected consistency with bound request context."""
        ctx = self._request_ctx
        if ctx is None:
            return
        
        has_req_id = bool(ctx.req_id and str(ctx.req_id).strip())
        provider = str(ctx.provider or '').strip().lower()
        is_gemini = provider == 'gemini'
        expected_is_hook = is_gemini and has_req_id
        
        if self._is_hook_expected and not expected_is_hook:
            logger.error(
                f"TD-008 validation failed: is_hook_expected=True but "
                f"req_id={'present' if has_req_id else 'missing'}, "
                f"provider={provider!r}"
            )
            raise ValueError(
                f"is_hook_expected=True inconsistent with runtime: "
                f"req_id={'present' if has_req_id else 'missing'}, "
                f"provider={provider!r}"
            )
        
        if not self._is_hook_expected and expected_is_hook:
            logger.error(
                f"TD-008 validation failed: is_hook_expected=False but "
                f"req_id={ctx.req_id!r}, provider={provider!r} "
                f"(should be True for Gemini with req_id)"
            )
            raise ValueError(
                f"is_hook_expected=False but Gemini with req_id detected: "
                f"req_id={ctx.req_id!r}"
            )

    def ingest(self, item: CompletionItem) -> None:
        self._require_bound()
        self._consume_common_item(item)

        if item.kind is CompletionItemKind.CANCEL_INFO:
            self._set_terminal(
                status=CompletionStatus.CANCELLED,
                reason=first_non_empty(item.payload, 'reason') or 'cancel_info',
                confidence=CompletionConfidence.OBSERVED,
                finished_at=item.timestamp,
            )
            return

        if item.kind in {CompletionItemKind.SESSION_SNAPSHOT, CompletionItemKind.SESSION_MUTATION}:
            raw_tool_calls = item.payload.get('tool_call_count')
            if raw_tool_calls is not None:
                try:
                    self._state.tool_active = int(raw_tool_calls) > 0
                except Exception:
                    self._state.tool_active = bool(raw_tool_calls)

            reply = first_non_empty(item.payload, 'reply', 'content', 'text')
            if reply:
                fingerprint = fingerprint_text(
                    first_non_empty(item.payload, 'message_id') or '',
                    reply,
                    item.payload.get('message_count'),
                    item.payload.get('last_updated'),
                )
                if fingerprint != self._state.last_reply_hash:
                    self._record_reply(item, reply, fingerprint=fingerprint)
                    self._state.stable_since = item.timestamp
                self._set_pending()
                return

        if item.kind is CompletionItemKind.ERROR:
            self._set_terminal(
                status=CompletionStatus.FAILED,
                reason=first_non_empty(item.payload, 'reason', 'error') or 'session_corrupt',
                confidence=CompletionConfidence.OBSERVED,
                finished_at=item.timestamp,
            )
            return

        if item.kind is CompletionItemKind.PANE_DEAD:
            self._set_terminal(
                status=CompletionStatus.FAILED,
                reason=first_non_empty(item.payload, 'reason') or 'pane_dead',
                confidence=CompletionConfidence.DEGRADED,
                finished_at=item.timestamp,
            )
            return

        self._set_pending()

    def tick(self, now: str, cursor: CompletionCursor | None = None) -> None:
        self._sync_cursor(cursor)
        if self._decision.terminal:
            return

        if not self._state.reply_started or self._state.stable_since is None:
            return

        if self._state.tool_active:
            self._set_pending()
            return

        if seconds_between(self._state.stable_since, now) >= self._settle_window_s:
            self._state.reply_stable = True
            
            # R5: observability - distinguish hook fallback from normal terminal
            if self._is_hook_expected:
                reason = 'session_reply_stable_hook_fallback'
                diagnostics: dict[str, Any] | None = {
                    'is_hook_expected': True,
                    'settle_window_s_used': self._settle_window_s,
                }
                logger.warning(
                    f"TD-008 hook fallback triggered: req_id={self._request_ctx.req_id if self._request_ctx else 'N/A'}, "
                    f"settle_window_s={self._settle_window_s}"
                )
            else:
                reason = 'session_reply_stable'
                diagnostics = None

            self._set_terminal(
                status=CompletionStatus.COMPLETED,
                reason=reason,
                confidence=CompletionConfidence.OBSERVED,
                finished_at=now,
                diagnostics=diagnostics,
            )
        else:
            self._set_pending()

    def finalize_timeout(self, now: str, cursor: CompletionCursor | None = None) -> None:
        self._require_bound()
        if self._decision.terminal:
            return
        self._sync_cursor(cursor)
        super().finalize_timeout(now, cursor)
