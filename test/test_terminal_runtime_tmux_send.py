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


def _count_enter_sends(calls: list[list[str]]) -> int:
    return sum(
        1 for c in calls
        if c[:2] == ['send-keys', '-t'] and len(c) == 4 and c[-1] == 'Enter'
    )


def _build_second_enter_sender(tmux_run_fn, *, second_enter_delay: float):
    def env(name: str, default: float) -> float:
        if name == 'CCB_TMUX_SECOND_ENTER_DELAY':
            return second_enter_delay
        if name == 'CCB_TMUX_ENTER_DELAY':
            return 0.0
        return default

    return TmuxTextSender(
        tmux_run_fn=tmux_run_fn,
        looks_like_tmux_target_fn=lambda _: True,
        ensure_not_in_copy_mode_fn=lambda _: None,
        build_buffer_name_fn=lambda **_: 'buf-se',
        sanitize_text_fn=lambda t: t,
        should_use_inline_legacy_send_fn=lambda **_: False,
        env_float_fn=env,
        sleep_fn=lambda _: None,
    )


def test_second_enter_off_by_default() -> None:
    """CCB_TMUX_SECOND_ENTER_DELAY=0 → exactly one Enter (stock behavior)."""
    calls: list[list[str]] = []
    _build_second_enter_sender(
        lambda args, **kwargs: calls.append(list(args)) or _cp(),
        second_enter_delay=0.0,
    ).send_text('%5', 'msg')
    assert _count_enter_sends(calls) == 1


def test_second_enter_fires_when_delay_positive() -> None:
    """CCB_TMUX_SECOND_ENTER_DELAY>0 → two Enters for cold-start CLI rescue."""
    calls: list[list[str]] = []
    _build_second_enter_sender(
        lambda args, **kwargs: calls.append(list(args)) or _cp(),
        second_enter_delay=2.0,
    ).send_text('%5', 'msg')
    assert _count_enter_sends(calls) == 2


def test_second_enter_ignores_negative_delay() -> None:
    """Negative values treated as off (guard)."""
    calls: list[list[str]] = []
    _build_second_enter_sender(
        lambda args, **kwargs: calls.append(list(args)) or _cp(),
        second_enter_delay=-1.0,
    ).send_text('%5', 'msg')
    assert _count_enter_sends(calls) == 1


# ===== Reception-driven mode tests (R2) =====

import json as _json_for_reception_tests  # avoid clash if file has its own json import


def _reception_env(name: str, default: float) -> float:
    """env_float_fn for reception-driven tests: 短超时 + 无延迟。"""
    if name == 'CCB_VERIFY_DELIVERY':
        return 0.0
    if name == 'CCB_RECEPTION_DRIVEN':
        return 1.0  # 默认开
    if name == 'CCB_RECEPTION_TIMEOUT_S':
        return 10.0  # 短超时方便测试
    if name == 'CCB_RECEPTION_POLL_INTERVAL_S':
        return 0.1
    if name == 'CCB_RECEPTION_MAX_ATTEMPTS':
        return 3.0
    if name in ('CCB_TMUX_ENTER_DELAY', 'CCB_TMUX_SECOND_ENTER_DELAY'):
        return 0.0  # 测试零延迟
    return default


def _build_reception_sender(tmux_run_fn, *, time_fn=None, env_fn=_reception_env):
    """构造一个 TmuxTextSender，自动生成单调 time + 0 sleep。"""
    if time_fn is None:
        clock = [0.0]
        def time_fn():
            clock[0] += 0.05
            return clock[0]
    return TmuxTextSender(
        tmux_run_fn=tmux_run_fn,
        looks_like_tmux_target_fn=lambda _: True,
        ensure_not_in_copy_mode_fn=lambda _: None,
        build_buffer_name_fn=lambda **_: 'buf-r',
        sanitize_text_fn=lambda t: t,
        should_use_inline_legacy_send_fn=lambda **_: False,
        env_float_fn=env_fn,
        sleep_fn=lambda _: None,
        time_fn=time_fn,
    )


def test_reception_path_A_present_at_first_poll(tmp_path):
    """路径 A：reception 文件 paste 后立刻就在 → 1 paste + Enter，无 retry。"""
    reception_root = tmp_path / "reception"
    (reception_root / "events").mkdir(parents=True)
    (reception_root / "events" / "job_test_a.json").write_text("{}")

    calls = []
    def tmux_run(args, **kw):
        calls.append(list(args))
        if args and args[0] == 'capture-pane':
            return _cp(stdout="")  # 不重要：reception 已存在
        return _cp()

    _build_reception_sender(tmux_run).send_text(
        '%5', 'CCB_REQ_ID: job_test_a do thing',
        req_id='job_test_a', reception_dir=reception_root,
    )

    paste_count = sum(1 for c in calls if c[:1] == ['paste-buffer'])
    assert paste_count == 1, f"expected 1 paste, got {paste_count}: {calls}"
    # 不应该有 retry 时的 Esc / C-u
    assert not any(c[-1] == 'Escape' for c in calls if 'send-keys' in c)


def test_reception_path_B_enter_swallowed_补enter_then_succeed(tmp_path):
    """路径 B：reception 第一次 poll 没出，但 pane 含 req_id → 补 Enter，第二次 poll 出现。"""
    reception_root = tmp_path / "reception"
    (reception_root / "events").mkdir(parents=True)
    artifact = reception_root / "events" / "job_test_b.json"

    poll_count = [0]
    enter_补_count = [0]
    calls = []
    def tmux_run(args, **kw):
        calls.append(list(args))
        if args and args[0] == 'capture-pane':
            poll_count[0] += 1
            if poll_count[0] >= 2:
                artifact.write_text("{}")  # 第二次 poll 时 reception 出现
            return _cp(stdout="> Type your message\n  CCB_REQ_ID: job_test_b do thing\n")
        if args[:2] == ['send-keys', '-t'] and len(args) == 4 and args[3] == 'Enter':
            enter_补_count[0] += 1  # 含初始 1 个 + 补的
        return _cp()

    _build_reception_sender(tmux_run).send_text(
        '%5', 'CCB_REQ_ID: job_test_b do thing',
        req_id='job_test_b', reception_dir=reception_root,
    )

    paste_count = sum(1 for c in calls if c[:1] == ['paste-buffer'])
    assert paste_count == 1, "应该只 paste 1 次（不 retry，只补 Enter）"
    assert enter_补_count[0] >= 2, "应该至少 1 个初始 Enter + 1 个补 Enter"


def test_reception_path_C_paste_lost_then_retry_succeeds(tmp_path):
    """路径 C：第一轮 pane 既无 activity 又无 req_id → break → 第二轮 paste，reception 出现。"""
    reception_root = tmp_path / "reception"
    (reception_root / "events").mkdir(parents=True)
    artifact = reception_root / "events" / "job_test_c.json"

    pane_responses = iter([
        "> Type your message\n",  # 第一轮 poll：完全没 paste 进去
        "> CCB_REQ_ID: job_test_c\n",  # 第二轮 paste 后：req_id 在
    ])
    poll_count = [0]
    calls = []
    def tmux_run(args, **kw):
        calls.append(list(args))
        if args and args[0] == 'capture-pane':
            poll_count[0] += 1
            try:
                response = next(pane_responses)
            except StopIteration:
                response = "> CCB_REQ_ID: job_test_c\n"
            if poll_count[0] >= 2:
                artifact.write_text("{}")
            return _cp(stdout=response)
        return _cp()

    _build_reception_sender(tmux_run).send_text(
        '%5', 'CCB_REQ_ID: job_test_c do thing',
        req_id='job_test_c', reception_dir=reception_root,
    )

    paste_count = sum(1 for c in calls if c[:1] == ['paste-buffer'])
    assert paste_count == 2, f"应该 paste 2 次（第一轮失败 + 第二轮重 paste），实际 {paste_count}"
    # 第二轮前应该有 Escape + C-u
    sk = [c for c in calls if c[:2] == ['send-keys', '-t']]
    assert any(c[-1] == 'Escape' for c in sk), "retry 前必须发 Escape"
    assert any(c[-1] == 'C-u' for c in sk), "retry 前必须发 C-u"


def test_reception_path_D_all_attempts_fail_raises(tmp_path):
    """路径 D：3 轮全部 paste 都没成功且 reception 始终不出现 → raise CcbDeliveryError。"""
    reception_root = tmp_path / "reception"
    (reception_root / "events").mkdir(parents=True)

    def tmux_run(args, **kw):
        if args and args[0] == 'capture-pane':
            return _cp(stdout="> Type your message\n")  # 永远没 req_id 也没 activity
        return _cp()

    with pytest.raises(CcbDeliveryError):
        _build_reception_sender(tmux_run).send_text(
            '%5', 'CCB_REQ_ID: job_test_d do thing',
            req_id='job_test_d', reception_dir=reception_root,
        )


def test_reception_path_E_no_req_id_falls_back_to_legacy(tmp_path):
    """路径 E：req_id=None → 走旧路径，绝不 poll capture-pane。"""
    calls = []
    def tmux_run(args, **kw):
        calls.append(list(args))
        return _cp()

    _build_reception_sender(tmux_run).send_text('%5', 'plain prompt')

    captures = [c for c in calls if c and c[0] == 'capture-pane']
    assert captures == [], f"旧路径不应该 capture-pane，实际: {captures}"


def test_reception_path_F_kill_switch_forces_legacy(tmp_path):
    """路径 F：CCB_RECEPTION_DRIVEN=0 → 强制走旧路径。"""
    reception_root = tmp_path / "reception"
    (reception_root / "events").mkdir(parents=True)

    def kill_switch_env(name, default):
        if name == 'CCB_RECEPTION_DRIVEN':
            return 0.0  # 关闭
        return _reception_env(name, default)

    calls = []
    def tmux_run(args, **kw):
        calls.append(list(args))
        return _cp()

    _build_reception_sender(tmux_run, env_fn=kill_switch_env).send_text(
        '%5', 'CCB_REQ_ID: job_test_f',
        req_id='job_test_f', reception_dir=reception_root,
    )

    captures = [c for c in calls if c and c[0] == 'capture-pane']
    assert captures == [], "kill switch 开启时不应 capture-pane"


def test_reception_path_G_agent_activity_returns_success_without_artifact(tmp_path):
    """路径 G：reception 文件不出，但 pane tail 含 'Planning' → 返回成功（绝不 retry）。"""
    reception_root = tmp_path / "reception"
    (reception_root / "events").mkdir(parents=True)

    calls = []
    def tmux_run(args, **kw):
        calls.append(list(args))
        if args and args[0] == 'capture-pane':
            return _cp(stdout="✦ Planning your request...\n  using tools\n")
        return _cp()

    # 不应 raise，且不应 retry
    _build_reception_sender(tmux_run).send_text(
        '%5', 'CCB_REQ_ID: job_test_g do thing',
        req_id='job_test_g', reception_dir=reception_root,
    )

    paste_count = sum(1 for c in calls if c[:1] == ['paste-buffer'])
    assert paste_count == 1, f"agent 活动确认后不应 retry paste，实际 {paste_count}"


def test_reception_path_H_old_scrollback_req_id_does_not_satisfy(tmp_path):
    """路径 H：req_id 在更老的 scrollback 但末 10 行没有 → 不视为 paste 成功 → retry。

    关键点：tmux_send 用 `capture-pane -S -10` 只取末 10 行；测试通过让 mock 仅返回不含 req_id 的内容来验证这个边界。
    """
    reception_root = tmp_path / "reception"
    (reception_root / "events").mkdir(parents=True)
    artifact = reception_root / "events" / "job_test_h.json"

    capture_args_seen = []
    poll_count = [0]
    def tmux_run(args, **kw):
        if args and args[0] == 'capture-pane':
            capture_args_seen.append(list(args))
            poll_count[0] += 1
            if poll_count[0] >= 3:
                artifact.write_text("{}")  # 让最终成功，避免无限 raise
            return _cp(stdout="> Type your message\n")  # 永远不含 req_id
        return _cp()

    _build_reception_sender(tmux_run).send_text(
        '%5', 'CCB_REQ_ID: job_test_h do thing',
        req_id='job_test_h', reception_dir=reception_root,
    )

    # 验证 capture-pane 命令包含 -S -10
    assert capture_args_seen, "至少应该 capture 过"
    sample = capture_args_seen[0]
    assert '-S' in sample and '-10' in sample, f"capture-pane 应限定末 10 行，实际命令: {sample}"

def test_reception_path_I_capture_failure_does_not_double_send(tmp_path):
    """Path I (regression guard): _capture_pane_tail 异常返回 '' 时，
    D3 不变量 + req_id 检测都得到 False，应走 break→retry 分支，
    绝不应进补 Enter 分支（防双发）。
    锁住当前正确行为：未来若有人改 D3 检查或 req_id 判断逻辑，
    必须保证此 path 仍走 retry。
    """
    reception_root = tmp_path / "reception"
    (reception_root / "events").mkdir(parents=True)
    artifact = reception_root / "events" / "job_test_i.json"
    call_count = [0]
    calls = []

    def tmux_run(args, **kw):
        calls.append(list(args))
        if args and args[0] == 'capture-pane':
            call_count[0] += 1
            if call_count[0] >= 3:
                # 第三轮终于成功 capture + reception 出现
                artifact.write_text("{}")
                return _cp(stdout="✦ Generating response\n")
            # 前两轮 capture 失败返回空 stdout（模拟 tmux exception 经 _capture_pane_tail 兜底）
            return _cp(stdout="")
        return _cp()

    _build_reception_sender(tmux_run).send_text(
        '%5', 'CCB_REQ_ID: job_test_i do thing',
        req_id='job_test_i', reception_dir=reception_root,
    )

    paste_count = sum(1 for c in calls if c[:1] == ['paste-buffer'])
    assert paste_count >= 2, "capture 失败两轮应触发至少 1 次 retry（重 paste），实际 paste {paste_count}".format(paste_count=paste_count)

    # 关键：retry 阶段必须有 Esc + C-u（验证走的是 retry 分支不是 补 Enter 分支）
    sk = [c for c in calls if c[:2] == ['send-keys', '-t']]
    assert any(c[-1] == 'Escape' for c in sk), "应走 retry 分支（含 Escape），不能误判进 补 Enter 分支"
