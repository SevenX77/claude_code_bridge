"""TD-008: AnchoredSessionStabilityDetector parameterized degradation tests."""
from __future__ import annotations
import pytest

from completion.detectors.anchored_session_stability import AnchoredSessionStabilityDetector
from completion.models import (
    CompletionConfidence,
    CompletionCursor,
    CompletionItem,
    CompletionItemKind,
    CompletionRequestContext,
    CompletionStatus,
)


def _make_context(*, req_id: str = "job_test123", provider: str = "gemini") -> CompletionRequestContext:
    """Create a CompletionRequestContext for testing."""
    return CompletionRequestContext(
        req_id=req_id,
        agent_name="a2",
        provider=provider,
        timeout_s=60.0,
    )


def _make_baseline() -> CompletionCursor:
    """Create a baseline cursor."""
    return CompletionCursor(
        source_kind="session",
        event_seq=0,
        updated_at="2026-04-23T00:00:00Z",
    )


def _make_snapshot_item(*, reply: str = "test reply", timestamp: str = "2026-04-23T00:00:01Z", provider: str = "gemini", req_id: str = "job_test123") -> CompletionItem:
    """Create a SESSION_SNAPSHOT item."""
    return CompletionItem(
        kind=CompletionItemKind.SESSION_SNAPSHOT,
        timestamp=timestamp,
        cursor=_make_baseline(),
        provider=provider,
        agent_name="a2",
        req_id=req_id,
        payload={
            "reply": reply,
            "tool_call_count": 0,
        },
    )


class TestIsHookExpected30sWindow:
    """Test 1: is_hook_expected=True uses 30s settle window."""

    def test_29s_stable_still_pending(self):
        """29s stable should still be pending (<30s)."""
        detector = AnchoredSessionStabilityDetector(is_hook_expected=True)
        ctx = _make_context()
        baseline = _make_baseline()
        detector.bind(ctx, baseline)
        
        # Feed snapshot at T+0
        item = _make_snapshot_item(timestamp="2026-04-23T00:00:00Z")
        detector.ingest(item)
        
        # Tick at T+29s
        detector.tick("2026-04-23T00:00:29Z")
        
        assert not detector.decision().terminal, "Should still be pending at 29s"
    
    def test_31s_stable_triggers_terminal(self):
        """31s stable should trigger terminal (>30s)."""
        detector = AnchoredSessionStabilityDetector(is_hook_expected=True)
        ctx = _make_context()
        baseline = _make_baseline()
        detector.bind(ctx, baseline)
        
        # Feed snapshot at T+0
        item = _make_snapshot_item(timestamp="2026-04-23T00:00:00Z")
        detector.ingest(item)
        
        # Tick at T+31s
        detector.tick("2026-04-23T00:00:31Z")
        
        decision = detector.decision()
        assert decision.terminal, "Should be terminal at 31s"
        assert decision.reason == "session_reply_stable_hook_fallback"
        assert decision.diagnostics.get("is_hook_expected") is True
        assert decision.diagnostics.get("settle_window_s_used") == 30.0


class TestNoHookExpected2sWindow:
    """Test 2: is_hook_expected=False (default) uses 2s settle window."""

    def test_1s_stable_still_pending(self):
        """1s stable should still be pending (<2s)."""
        detector = AnchoredSessionStabilityDetector()  # default is_hook_expected=False
        ctx = _make_context(provider="claude")  # Use claude to avoid is_hook_expected conflict
        baseline = _make_baseline()
        detector.bind(ctx, baseline)
        
        item = _make_snapshot_item(timestamp="2026-04-23T00:00:00Z", provider="claude")
        detector.ingest(item)
        
        detector.tick("2026-04-23T00:00:01Z")
        
        assert not detector.decision().terminal
    
    def test_2s_stable_triggers_terminal(self):
        """2s stable should trigger terminal."""
        detector = AnchoredSessionStabilityDetector()
        ctx = _make_context(provider="claude")
        baseline = _make_baseline()
        detector.bind(ctx, baseline)
        
        item = _make_snapshot_item(timestamp="2026-04-23T00:00:00Z", provider="claude")
        detector.ingest(item)
        
        detector.tick("2026-04-23T00:00:02Z")
        
        decision = detector.decision()
        assert decision.terminal
        assert decision.reason == "session_reply_stable"


class TestExplicitSettleWindowOverride:
    """Test 3: Explicit settle_window_s overrides is_hook_expected."""

    def test_explicit_1s_overrides_30s(self):
        """settle_window_s=1.0 should take precedence over is_hook_expected=True."""
        detector = AnchoredSessionStabilityDetector(
            settle_window_s=1.0,
            is_hook_expected=True,
        )
        ctx = _make_context()
        baseline = _make_baseline()
        detector.bind(ctx, baseline)
        
        item = _make_snapshot_item(timestamp="2026-04-23T00:00:00Z")
        detector.ingest(item)
        
        # Should trigger at 1s, not 30s
        detector.tick("2026-04-23T00:00:01.1Z")
        
        decision = detector.decision()
        assert decision.terminal
        # When explicit settle_window is used with is_hook_expected, 
        # reason should still indicate hook_fallback
        assert "hook_fallback" in decision.reason or decision.reason == "session_reply_stable_hook_fallback"


class TestValidationConsistency:
    """Test defensive validation: is_hook_expected vs runtime consistency."""

    def test_hook_expected_true_non_gemini_raises(self):
        """is_hook_expected=True but non-Gemini provider should raise ValueError."""
        detector = AnchoredSessionStabilityDetector(is_hook_expected=True)
        
        # Provider claude - should raise because is_hook_expected=True only valid for Gemini
        with pytest.raises(ValueError, match="is_hook_expected=True"):
            detector.bind(_make_context(provider="claude"), _make_baseline())
    
    def test_hook_expected_false_with_req_id_raises(self):
        """is_hook_expected=False but Gemini with req_id should raise ValueError."""
        detector = AnchoredSessionStabilityDetector(is_hook_expected=False)
        
        with pytest.raises(ValueError, match="is_hook_expected=False but Gemini"):
            detector.bind(_make_context(req_id="job_test123", provider="gemini"), _make_baseline())
    
    def test_hook_expected_true_gemini_with_req_id_ok(self):
        """is_hook_expected=True + Gemini + req_id should be valid."""
        detector = AnchoredSessionStabilityDetector(is_hook_expected=True)
        
        # Should not raise
        detector.bind(_make_context(req_id="job_test123", provider="gemini"), _make_baseline())
        
        assert detector._is_hook_expected is True
        assert detector._settle_window_s == 30.0
    
    def test_no_hook_expected_claude_ok(self):
        """is_hook_expected=False + Claude should be valid (no req_id needed)."""
        detector = AnchoredSessionStabilityDetector(is_hook_expected=False)
        
        detector.bind(_make_context(req_id="job_test", provider="claude"), _make_baseline())
        
        assert detector._is_hook_expected is False
        assert detector._settle_window_s == 2.0


class TestR3MultipleTurnsIndependence:
    """Test R3: Multiple asks in same session should have independent detectors."""

    def test_two_detectors_independent(self):
        """Two separate detectors should have independent state."""
        # Use claude provider to avoid validation issues
        ctx = _make_context(provider="claude")
        baseline = _make_baseline()
        
        # First detector
        d1 = AnchoredSessionStabilityDetector()
        d1.bind(ctx, baseline)
        d1.ingest(_make_snapshot_item(reply="first reply", provider="claude"))
        
        # Second detector (new instance)
        d2 = AnchoredSessionStabilityDetector()
        d2.bind(ctx, baseline)
        d2.ingest(_make_snapshot_item(reply="second reply", provider="claude"))
        
        # Both should track independently (different hashes)
        assert d1.state().last_reply_hash != d2.state().last_reply_hash


class TestR4ManualAttachRegression:
    """Test R4: Manual attach (non-Gemini) should use 2s window."""

    def test_claude_manual_attach_2s_behavior(self):
        """Simulate user manual attach to Claude - should use 2s settle window."""
        detector = AnchoredSessionStabilityDetector(is_hook_expected=False)
        
        ctx = _make_context(provider="claude")
        detector.bind(ctx, _make_baseline())
        
        item = _make_snapshot_item(timestamp="2026-04-23T00:00:00Z", provider="claude")
        detector.ingest(item)
        
        detector.tick("2026-04-23T00:00:02Z")
        
        assert detector.decision().terminal
        assert detector.decision().reason == "session_reply_stable"
