from __future__ import annotations

import subprocess

import pytest

from terminal_runtime.tmux_send import CcbDeliveryError, TmuxTextSender


def _verify_env(name: str, default: float) -> float:
    """env_float_fn that opts into CCB_VERIFY_DELIVERY, leaves delays at default."""
    if name == 'CCB_VERIFY_DELIVERY':
        return 1.0
    if name in ('CCB_TMUX_ENTER_DELAY', 'CCB_VERIFY_POST_DELAY_MS'):
        return 0.0  # zero delay for test speed
    return default


def _build_verifying_sender(tmux_run_fn):
    return TmuxTextSender(
        tmux_run_fn=tmux_run_fn,
        looks_like_tmux_target_fn=lambda _: True,
        ensure_not_in_copy_mode_fn=lambda _: None,
        build_buffer_name_fn=lambda **_: 'buf-v',
        sanitize_text_fn=lambda t: t,
        should_use_inline_legacy_send_fn=lambda **_: False,
        env_float_fn=_verify_env,
        sleep_fn=lambda _: None,
    )


def _cp(*, stdout: str = '', returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=['tmux'], returncode=returncode, stdout=stdout, stderr='')


def test_tmux_text_sender_deletes_buffer_after_paste_failure() -> None:
    calls: list[list[str]] = []
    sender = TmuxTextSender(
        tmux_run_fn=lambda args, **kwargs: calls.append(args) or (
            (_ for _ in ()).throw(subprocess.CalledProcessError(1, ['tmux', *args]))
            if args and args[0] == 'paste-buffer'
            else _cp()
        ),
        looks_like_tmux_target_fn=lambda value: True,
        ensure_not_in_copy_mode_fn=lambda pane_id: calls.append(['ensure-copy-mode', pane_id]),
        build_buffer_name_fn=lambda **kwargs: 'buf-1',
        sanitize_text_fn=lambda text: text,
        should_use_inline_legacy_send_fn=lambda **kwargs: False,
        env_float_fn=lambda name, default: 0.0,
    )

    with pytest.raises(subprocess.CalledProcessError):
        sender.send_text('%1', 'hello')

    assert calls[0] == ['ensure-copy-mode', '%1']
    assert ['load-buffer', '-b', 'buf-1', '-'] in calls
    assert ['paste-buffer', '-p', '-t', '%1', '-b', 'buf-1'] in calls
    assert calls[-1] == ['delete-buffer', '-b', 'buf-1']


def test_tmux_text_sender_uses_inline_legacy_mode_for_session_targets() -> None:
    calls: list[list[str]] = []
    sender = TmuxTextSender(
        tmux_run_fn=lambda args, **kwargs: calls.append(args) or _cp(),
        looks_like_tmux_target_fn=lambda value: False,
        ensure_not_in_copy_mode_fn=lambda pane_id: None,
        build_buffer_name_fn=lambda **kwargs: 'buf-2',
        sanitize_text_fn=lambda text: text,
        should_use_inline_legacy_send_fn=lambda **kwargs: True,
        env_float_fn=lambda name, default: 0.0,
    )

    sender.send_text('session-x', 'hello')

    assert calls == [
        ['send-keys', '-t', 'session-x', '-l', 'hello'],
        ['send-keys', '-t', 'session-x', 'Enter'],
    ]


def test_verify_disabled_by_default_no_capture_calls() -> None:
    """CCB_VERIFY_DELIVERY unset → no capture-pane calls, no behavior diff vs stock."""
    calls: list[list[str]] = []
    sender = TmuxTextSender(
        tmux_run_fn=lambda args, **kwargs: calls.append(args) or _cp(),
        looks_like_tmux_target_fn=lambda _: True,
        ensure_not_in_copy_mode_fn=lambda _: None,
        build_buffer_name_fn=lambda **_: 'buf-d',
        sanitize_text_fn=lambda t: t,
        should_use_inline_legacy_send_fn=lambda **_: False,
        env_float_fn=lambda name, default: 0.0,  # verify OFF
        sleep_fn=lambda _: None,
    )
    sender.send_text('%9', 'hi')
    assert not any(c and c[0] == 'capture-pane' for c in calls)


def test_verify_success_when_prompt_changes_after_enter() -> None:
    """Pre-fp != post-fp → submission succeeded, no retry keycodes fired."""
    capture_responses = iter([
        'Type your message',    # pre (idle prompt)
        'Thinking... 5s',       # post (gemini working)
    ])
    calls: list[list[str]] = []

    def tmux_run(args, **kwargs):
        calls.append(list(args))
        if args and args[0] == 'capture-pane':
            return _cp(stdout=next(capture_responses))
        return _cp()

    _build_verifying_sender(tmux_run).send_text('%5', 'hello')

    retry_sends = [c for c in calls if c[:2] == ['send-keys', '-t'] and len(c) == 4 and c[3] in ('Return', 'C-m')]
    assert retry_sends == []


def test_verify_retries_return_then_c_m_on_unchanged_prompt() -> None:
    """Pre == post after Enter, pre still == after Return, differs after C-m → success after C-m retry."""
    capture_responses = iter([
        'Type your message',    # pre
        'Type your message',    # post after Enter (unchanged → retry)
        'Type your message',    # after Return (still unchanged → try C-m)
        'Thinking... 1s',       # after C-m (success)
    ])
    calls: list[list[str]] = []

    def tmux_run(args, **kwargs):
        calls.append(list(args))
        if args and args[0] == 'capture-pane':
            return _cp(stdout=next(capture_responses))
        return _cp()

    _build_verifying_sender(tmux_run).send_text('%5', 'msg')

    retry_keys = [c[-1] for c in calls if c[:2] == ['send-keys', '-t'] and len(c) == 4 and c[-1] in ('Return', 'C-m')]
    assert retry_keys == ['Return', 'C-m']


def test_verify_raises_when_all_retries_fail() -> None:
    """Every capture matches pre-fp → CcbDeliveryError after retries."""
    capture_responses = iter([
        'Type your message',    # pre
        'Type your message',    # post Enter
        'Type your message',    # after Return
        'Type your message',    # after C-m
    ])

    def tmux_run(args, **kwargs):
        if args and args[0] == 'capture-pane':
            return _cp(stdout=next(capture_responses))
        return _cp()

    with pytest.raises(CcbDeliveryError):
        _build_verifying_sender(tmux_run).send_text('%5', 'msg')


def test_verify_soft_skips_when_post_capture_empty() -> None:
    """capture-pane returning empty text after Enter (unknown state) → soft skip, no retry, no raise."""
    capture_responses = iter([
        'Type your message',    # pre
        '',                     # post: capture returned empty (tmux error)
    ])
    calls: list[list[str]] = []

    def tmux_run(args, **kwargs):
        calls.append(list(args))
        if args and args[0] == 'capture-pane':
            return _cp(stdout=next(capture_responses))
        return _cp()

    _build_verifying_sender(tmux_run).send_text('%5', 'msg')

    retry_sends = [c for c in calls if c[:2] == ['send-keys', '-t'] and len(c) == 4 and c[-1] in ('Return', 'C-m')]
    assert retry_sends == []
