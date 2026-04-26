from __future__ import annotations

from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from cli.models import ParsedAskWaitCommand
from cli.phase2 import maybe_handle_phase2
from cli.phase2_runtime.handlers_ask import handle_ask_wait
from cli.services.ask import AskSummary


def test_phase2_ask_wait_submit_writes_output(monkeypatch, tmp_path: Path) -> None:
    import cli.phase2 as phase2_module

    fake_context = SimpleNamespace(project=SimpleNamespace(project_root=tmp_path, project_id='proj-1'))

    monkeypatch.setattr(phase2_module, '_build_context', lambda command, cwd, out: fake_context)
    monkeypatch.setattr(phase2_module, 'ensure_bootstrap_project_config', lambda project_root: None)
    monkeypatch.setattr(
        phase2_module,
        'submit_ask',
        lambda context, command: AskSummary(
            project_id='proj-1',
            submission_id=None,
            jobs=({'job_id': 'job_1', 'target_name': 'agent1', 'status': 'accepted'},),
        ),
    )
    monkeypatch.setattr(
        phase2_module,
        'watch_ask_job',
        lambda context, job_id, out, timeout, emit_output, command=None: SimpleNamespace(status='completed', reply='done'),
    )

    output_path = tmp_path / 'reply.txt'
    stdout = StringIO()
    stderr = StringIO()
    code = maybe_handle_phase2(
        ['ask', '--wait', '--output', str(output_path), 'agent1', 'hello'],
        cwd=tmp_path,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    assert stdout.getvalue() == ''
    assert stderr.getvalue() == ''
    assert output_path.read_text(encoding='utf-8') == 'done\n'


def test_handle_ask_wait_slash_job_id_completes_without_watch() -> None:
    calls: list[str] = []
    command = ParsedAskWaitCommand(project=None, job_id='/clear')

    def watch_ask_job(context, job_id, out, timeout, emit_output, command=None):
        del context, job_id, out, timeout, emit_output, command
        calls.append('watch')
        raise AssertionError('slash job id must not watch')

    services = SimpleNamespace(
        watch_ask_job=watch_ask_job,
        is_slash_ask_command=lambda command: getattr(command, 'message', '') == '/clear',
        exit_code_for_ask_status=lambda status, reply: 0 if status == 'completed' and reply == '' else 1,
    )

    code = handle_ask_wait(SimpleNamespace(), command, StringIO(), services)

    assert code == 0
    assert calls == []


def test_handle_ask_wait_passes_command_to_watch() -> None:
    captured: dict[str, object] = {}
    command = ParsedAskWaitCommand(project=None, job_id='job_1', timeout_s=12.5)

    def watch_ask_job(context, job_id, out, timeout, emit_output, command=None):
        del context, out
        captured['job_id'] = job_id
        captured['timeout'] = timeout
        captured['emit_output'] = emit_output
        captured['command'] = command
        return SimpleNamespace(status='completed', reply='done')

    services = SimpleNamespace(
        watch_ask_job=watch_ask_job,
        is_slash_ask_command=lambda command: False,
        exit_code_for_ask_status=lambda status, reply: 0 if status == 'completed' and reply == 'done' else 1,
    )

    code = handle_ask_wait(SimpleNamespace(), command, StringIO(), services)

    assert code == 0
    assert captured == {
        'job_id': 'job_1',
        'timeout': 12.5,
        'emit_output': True,
        'command': command,
    }
