"""TD-008 Integration tests: Mocked Gemini completion scenarios."""
from __future__ import annotations
import pytest
from unittest.mock import MagicMock, patch

from completion.detectors.anchored_session_stability import AnchoredSessionStabilityDetector
from completion.models import (
    CompletionConfidence,
    CompletionCursor,
    CompletionItem,
    CompletionItemKind,
    CompletionRequestContext,
    CompletionStatus,
)


@pytest.mark.integration
def test_gemini_with_hook_expected_waits_for_hook():
    """Test 5 (mocked): When hook is expected, detector should wait 30s."""
    detector = AnchoredSessionStabilityDetector(is_hook_expected=True)
    ctx = CompletionRequestContext(
        req_id="job_gemini_001",
        agent_name="a2",
        provider="gemini",
        timeout_s=60.0,
    )
    baseline = CompletionCursor(
        source_kind="session",
        event_seq=0,
        updated_at="2026-04-23T00:00:00Z",
    )
    detector.bind(ctx, baseline)
    
    # Simulate session snapshot with tool announce (like yesterday's bug)
    item = CompletionItem(
        kind=CompletionItemKind.SESSION_SNAPSHOT,
        timestamp="2026-04-23T00:00:00Z",
        cursor=baseline,
        provider="gemini",
        agent_name="a2",
        req_id="job_gemini_001",
        payload={
            "reply": "我将搜索这些文件以确定其准确位置。",
            "tool_call_count": 0,
        },
    )
    detector.ingest(item)
    
    # At T+10s, should still be pending (not terminal yet)
    detector.tick("2026-04-23T00:00:10Z")
    assert not detector.decision().terminal, "Should wait for hook, not terminal at 10s"
    
    # At T+31s, should trigger hook_fallback terminal
    detector.tick("2026-04-23T00:00:31Z")
    decision = detector.decision()
    assert decision.terminal
    assert decision.reason == "session_reply_stable_hook_fallback"
    assert decision.diagnostics.get("is_hook_expected") is True


@pytest.mark.integration
def test_gemini_without_hook_expected_2s_terminal():
    """Test 6 (mocked): Manual attach without is_hook_expected uses 2s."""
    detector = AnchoredSessionStabilityDetector(is_hook_expected=False)
    ctx = CompletionRequestContext(
        req_id="manual_user",
        agent_name="a2",
        provider="claude",  # Non-gemini
        timeout_s=60.0,
    )
    baseline = CompletionCursor(
        source_kind="session",
        event_seq=0,
        updated_at="2026-04-23T00:00:00Z",
    )
    detector.bind(ctx, baseline)
    
    item = CompletionItem(
        kind=CompletionItemKind.SESSION_SNAPSHOT,
        timestamp="2026-04-23T00:00:00Z",
        cursor=baseline,
        provider="claude",
        agent_name="a2",
        req_id="manual_user",
        payload={
            "reply": "Manual user reply",
            "tool_call_count": 0,
        },
    )
    detector.ingest(item)
    
    # At T+2s, should terminal with normal reason
    detector.tick("2026-04-23T00:00:02Z")
    decision = detector.decision()
    assert decision.terminal
    assert decision.reason == "session_reply_stable"
    assert decision.diagnostics is None or decision.diagnostics == {}


@pytest.mark.integration  
def test_detector_per_job_isolation():
    """Test 7: Multiple jobs have isolated detector state."""
    # Job 1
    ctx1 = CompletionRequestContext(
        req_id="job_001",
        agent_name="a2",
        provider="gemini",
        timeout_s=60.0,
    )
    baseline = CompletionCursor(
        source_kind="session",
        event_seq=0,
        updated_at="2026-04-23T00:00:00Z",
    )
    
    detector1 = AnchoredSessionStabilityDetector(is_hook_expected=True)
    detector1.bind(ctx1, baseline)
    detector1.ingest(CompletionItem(
        kind=CompletionItemKind.SESSION_SNAPSHOT,
        timestamp="2026-04-23T00:00:00Z",
        cursor=baseline,
        provider="gemini",
        agent_name="a2",
        req_id="job_001",
        payload={"reply": "First job reply", "tool_call_count": 0},
    ))
    
    # Job 2 (new detector instance)
    ctx2 = CompletionRequestContext(
        req_id="job_002",
        agent_name="a2",
        provider="gemini",
        timeout_s=60.0,
    )
    detector2 = AnchoredSessionStabilityDetector(is_hook_expected=True)
    detector2.bind(ctx2, baseline)
    detector2.ingest(CompletionItem(
        kind=CompletionItemKind.SESSION_SNAPSHOT,
        timestamp="2026-04-23T00:00:00Z",
        cursor=baseline,
        provider="gemini",
        agent_name="a2",
        req_id="job_002",
        payload={"reply": "Second job reply", "tool_call_count": 0},
    ))
    
    # Both should have independent state
    assert detector1.state().last_reply_hash != detector2.state().last_reply_hash
    
    # Job 1 tick at 10s - should not be terminal
    detector1.tick("2026-04-23T00:00:10Z")
    assert not detector1.decision().terminal
    
    # Job 2 tick at 31s - should be terminal
    detector2.tick("2026-04-23T00:00:31Z")
    assert detector2.decision().terminal
    
    # Job 1 should still not be terminal (isolated)
    assert not detector1.decision().terminal
