from __future__ import annotations

from dataclasses import dataclass
from agents.models import AgentSpec
from completion.models import (
    CompletionFamily,
    CompletionProfile,
    CompletionSourceKind,
    CompletionValidationError,
    SelectorFamily,
)


@dataclass(frozen=True)
class CompletionManifest:
    provider: str
    runtime_mode: str
    completion_family: CompletionFamily
    completion_source_kind: CompletionSourceKind
    supports_exact_completion: bool
    supports_observed_completion: bool
    supports_anchor_binding: bool
    supports_reply_stability: bool
    supports_terminal_reason: bool
    selector_family: SelectorFamily


def build_completion_profile(
    agent_spec: AgentSpec,
    manifest: CompletionManifest,
    *,
    is_hook_expected: bool = False,
) -> CompletionProfile:
    """Build a CompletionProfile from agent spec and manifest.
    
    Args:
        agent_spec: The agent specification
        manifest: The completion manifest from provider catalog
        is_hook_expected: Whether hook is expected to fire (TD-008)
            True for Gemini with req_id, False otherwise
    """
    if agent_spec.provider != manifest.provider:
        raise CompletionValidationError(
            f'agent provider {agent_spec.provider!r} does not match '
            f'manifest provider {manifest.provider!r}'
        )
    if agent_spec.runtime_mode.value != manifest.runtime_mode:
        raise CompletionValidationError(
            f'agent runtime_mode {agent_spec.runtime_mode.value!r} does not match '
            f'manifest runtime_mode {manifest.runtime_mode!r}'
        )
    
    return CompletionProfile(
        provider=manifest.provider,
        runtime_mode=agent_spec.runtime_mode,
        completion_family=manifest.completion_family,
        completion_source_kind=manifest.completion_source_kind,
        supports_exact_completion=manifest.supports_exact_completion,
        supports_observed_completion=manifest.supports_observed_completion,
        supports_anchor_binding=manifest.supports_anchor_binding,
        supports_reply_stability=manifest.supports_reply_stability,
        supports_terminal_reason=manifest.supports_terminal_reason,
        selector_family=manifest.selector_family,
        is_hook_expected=is_hook_expected,
    )
