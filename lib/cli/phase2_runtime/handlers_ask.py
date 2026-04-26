from __future__ import annotations

from types import SimpleNamespace


def handle_ask(context, command, out, services) -> int:
    if services.is_slash_ask_command(command):
        if command.output_path is not None:
            services.write_ask_output(command.output_path, '')
        return services.exit_code_for_ask_status('completed', reply='')

    summary = services.submit_ask(context, command)
    if not command.wait:
        services.write_lines(out, services.render_ask(summary))
        return 0
    if len(summary.jobs) != 1:
        raise RuntimeError('ccb ask --wait requires exactly one accepted job')

    job = summary.jobs[0]
    if job.get('status') == 'completed':
        reply = job.get('reply') or ''
        if command.output_path is not None:
            services.write_ask_output(command.output_path, reply)
        return services.exit_code_for_ask_status('completed', reply=reply)

    terminal = services.watch_ask_job(
        context,
        job['job_id'],
        out,
        timeout=command.timeout_s,
        emit_output=command.output_path is None,
        command=command,
    )
    reply = terminal.reply or ''
    if command.output_path is not None:
        services.write_ask_output(command.output_path, reply)
    return services.exit_code_for_ask_status(terminal.status, reply=reply)


def handle_ask_wait(context, command, out, services) -> int:
    if services.is_slash_ask_command(SimpleNamespace(message=command.job_id)):
        return services.exit_code_for_ask_status('completed', reply='')

    terminal = services.watch_ask_job(
        context,
        command.job_id,
        out,
        timeout=command.timeout_s,
        emit_output=True,
        command=command,
    )
    return services.exit_code_for_ask_status(terminal.status, reply=terminal.reply or '')


__all__ = ['handle_ask', 'handle_ask_wait']
