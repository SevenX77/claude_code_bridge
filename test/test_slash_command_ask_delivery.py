from __future__ import annotations

import subprocess
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from ccbd.api_models import DeliveryScope, JobRecord, JobStatus, MessageEnvelope
from cli.models import ParsedAskCommand
from cli.phase2_runtime.handlers_ask import handle_ask
from cli.services.ask_runtime.models import AskSummary
from provider_backends.codex.execution_runtime.start import start_active_submission as start_codex_submission
from provider_backends.gemini.execution_runtime.polling_runtime.reader import (
    poll_submission as poll_gemini_submission,
)
from provider_backends.gemini.execution_runtime.start_runtime.service import (
    start_active_submission as start_gemini_submission,
)
from provider_execution.base import ProviderRuntimeContext
from terminal_runtime.tmux_send import TmuxTextSender


def _job(*, provider: str, body: str = '/clear\n') -> JobRecord:
    return JobRecord(
        job_id='job_1',
        submission_id='sub_1',
        agent_name='agent1',
        provider=provider,
        request=MessageEnvelope(
            project_id='proj_1',
            to_agent='agent1',
            from_actor='cmd',
            body=body,
            task_id=None,
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        ),
        status=JobStatus.ACCEPTED,
        terminal_decision=None,
        cancel_requested_at=None,
        created_at='2026-04-26T00:00:00Z',
        updated_at='2026-04-26T00:00:00Z',
        workspace_path='/tmp/repo',
    )


def _context(tmp_path: Path) -> ProviderRuntimeContext:
    return ProviderRuntimeContext(
        agent_name='agent1',
        workspace_path=str(tmp_path),
        backend_type='tmux',
        runtime_ref=None,
        session_ref=None,
    )


def test_gemini_start_treats_slash_command_as_no_wrap_before_prompt_wrapping(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def tmux_run(args, **kwargs):
        calls.append(list(args))
        return subprocess.CompletedProcess(args=['tmux', *args], returncode=0, stdout='', stderr='')

    sender = TmuxTextSender(
        tmux_run_fn=tmux_run,
        looks_like_tmux_target_fn=lambda pane_id: True,
        ensure_not_in_copy_mode_fn=lambda pane_id: None,
        build_buffer_name_fn=lambda **kwargs: 'buf-slash',
        sanitize_text_fn=lambda text: text,
        should_use_inline_legacy_send_fn=lambda **kwargs: False,
        env_float_fn=lambda name, default: 0.0,
        sleep_fn=lambda seconds: None,
    )

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            sender.send_text(pane_id, text)

        def is_alive(self, pane_id: str) -> bool:
            return True

    class FakeSession:
        data = {}
        gemini_session_path = ''
        work_dir = str(tmp_path)

        def ensure_pane(self):
            return True, '%3'

    class FakeReader:
        def capture_state(self):
            return {'session_path': ''}

        def try_get_message(self, state):
            return None, state

    submission = start_gemini_submission(
        SimpleNamespace(provider='gemini'),
        _job(provider='gemini'),
        context=_context(tmp_path),
        now='2026-04-26T00:00:00Z',
        load_session_fn=lambda work_dir, agent_name: FakeSession(),
        backend_for_session_fn=lambda data: FakeBackend(),
        reader_factory=lambda session: FakeReader(),
        request_anchor_fn=lambda job_id: 'req_1',
        wrap_prompt_fn=lambda message, req_id: (_ for _ in ()).throw(AssertionError('slash must not wrap')),
    )

    assert submission.runtime_state['no_wrap'] is True
    assert submission.runtime_state['anchor_emitted'] is True
    assert submission.runtime_state['prompt_text'] == '/clear\n'
    assert 'CCB_REQ_ID:' not in submission.runtime_state['prompt_text']
    assert '#req_id' not in submission.runtime_state['prompt_text']

    poll_gemini_submission(
        submission,
        now='2026-04-26T00:00:01Z',
        extract_reply_for_req_fn=lambda reply, req_id: None,
        is_done_text_fn=lambda text, req_id: False,
        strip_done_text_fn=lambda text, req_id: text,
    )

    assert calls == [
        ['send-keys', '-t', '%3', '-l', '/clear'],
        ['send-keys', '-t', '%3', 'Enter'],
    ]
    assert not any(call and call[0] == 'paste-buffer' for call in calls)


def test_ask_wait_slash_command_completes_without_watch_polling(tmp_path: Path) -> None:
    calls: list[str] = []
    output_path = tmp_path / 'slash.out'
    command = ParsedAskCommand(
        project=None,
        target='agent1',
        sender=None,
        message='/clear',
        wait=True,
        output_path=str(output_path),
    )

    def submit_ask(context, command):
        del context, command
        calls.append('submit')
        raise AssertionError('slash command must not submit')

    def watch_ask_job(context, job_id, out, timeout, emit_output, command=None):
        del context, job_id, out, timeout, emit_output, command
        calls.append('watch')
        raise AssertionError('slash command must not watch')

    services = SimpleNamespace(
        submit_ask=submit_ask,
        watch_ask_job=watch_ask_job,
        is_slash_ask_command=lambda command: True,
        write_ask_output=lambda path, reply: Path(path).write_text(reply, encoding='utf-8'),
        exit_code_for_ask_status=lambda status, reply: 0 if status == 'completed' and reply == '' else 1,
    )

    code = handle_ask(SimpleNamespace(), command, StringIO(), services)

    assert code == 0
    assert calls == []
    assert output_path.read_text(encoding='utf-8') == ''


def test_ask_wait_completed_submit_does_not_enter_watch(tmp_path: Path) -> None:
    calls: list[str] = []
    output_path = tmp_path / 'completed.out'
    command = ParsedAskCommand(
        project=None,
        target='agent1',
        sender=None,
        message='hello',
        wait=True,
        output_path=str(output_path),
    )

    def submit_ask(context, command):
        del context, command
        calls.append('submit')
        return AskSummary(
            project_id='proj_1',
            submission_id='sub_1',
            jobs=({'job_id': 'job_1', 'agent_name': 'agent1', 'status': 'completed', 'reply': 'done'},),
        )

    def watch_ask_job(context, job_id, out, timeout, emit_output, command=None):
        del context, job_id, out, timeout, emit_output, command
        calls.append('watch')
        raise AssertionError('completed submission must not watch')

    services = SimpleNamespace(
        submit_ask=submit_ask,
        watch_ask_job=watch_ask_job,
        is_slash_ask_command=lambda command: False,
        write_ask_output=lambda path, reply: Path(path).write_text(reply, encoding='utf-8'),
        exit_code_for_ask_status=lambda status, reply: 0 if status == 'completed' and reply == 'done' else 1,
    )

    code = handle_ask(SimpleNamespace(), command, StringIO(), services)

    assert code == 0
    assert calls == ['submit']
    assert output_path.read_text(encoding='utf-8') == 'done'


def test_codex_slash_command_reaches_tmux_sender_as_raw_keystrokes(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def tmux_run(args, **kwargs):
        calls.append(list(args))
        return subprocess.CompletedProcess(args=['tmux', *args], returncode=0, stdout='', stderr='')

    sender = TmuxTextSender(
        tmux_run_fn=tmux_run,
        looks_like_tmux_target_fn=lambda pane_id: True,
        ensure_not_in_copy_mode_fn=lambda pane_id: None,
        build_buffer_name_fn=lambda **kwargs: 'buf-slash',
        sanitize_text_fn=lambda text: text,
        should_use_inline_legacy_send_fn=lambda **kwargs: False,
        env_float_fn=lambda name, default: 0.0,
        sleep_fn=lambda seconds: None,
    )

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            sender.send_text(pane_id, text)

    class FakeSession:
        data = {}
        codex_session_path = ''
        codex_session_id = ''
        work_dir = str(tmp_path)

        def ensure_pane(self):
            return True, '%1'

    class FakeReader:
        def capture_state(self):
            return {'log_path': ''}

    submission = start_codex_submission(
        SimpleNamespace(provider='codex'),
        _job(provider='codex'),
        context=_context(tmp_path),
        now='2026-04-26T00:00:00Z',
        load_session_fn=lambda work_dir, agent_name: FakeSession(),
        backend_for_session_fn=lambda data: FakeBackend(),
        reader_factory=lambda session, preferred_log: FakeReader(),
        request_anchor_fn=lambda job_id: 'req_1',
        wrap_prompt_fn=lambda message, req_id: (_ for _ in ()).throw(AssertionError('slash must not wrap')),
    )

    assert submission.runtime_state['no_wrap'] is True
    assert calls == [
        ['send-keys', '-t', '%1', '-l', '/clear'],
        ['send-keys', '-t', '%1', 'Enter'],
    ]
    assert not any(call and call[0] == 'paste-buffer' for call in calls)
