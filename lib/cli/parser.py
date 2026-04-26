from __future__ import annotations

import sys
from typing import Iterable

from stdio_runtime import read_stdin_text
from cli.models import ParsedCommand, ParsedStartCommand

from .parser_runtime import (
    SUBCOMMANDS,
    WAIT_COMMAND_TO_MODE,
    parse_ack,
    parse_ask,
    parse_cancel,
    parse_config,
    parse_doctor,
    parse_fault,
    parse_global_options,
    parse_inbox,
    parse_kill,
    parse_logs,
    parse_pend,
    parse_ping,
    parse_ps,
    parse_queue,
    parse_resubmit,
    parse_retry,
    parse_start,
    parse_trace,
    parse_wait,
    parse_watch,
)


class CliUsageError(ValueError):
    pass


_COMMAND_PARSERS = {
    'cancel': parse_cancel,
    'kill': parse_kill,
    'ps': parse_ps,
    'ping': parse_ping,
    'watch': parse_watch,
    'pend': parse_pend,
    'queue': parse_queue,
    'trace': parse_trace,
    'resubmit': parse_resubmit,
    'retry': parse_retry,
    'inbox': parse_inbox,
    'ack': parse_ack,
    'logs': parse_logs,
    'doctor': parse_doctor,
    'config': parse_config,
    'fault': parse_fault,
}


class CliParser:
    def parse(self, argv: Iterable[str]) -> ParsedCommand:
        tokens = list(argv)
        project, tokens = parse_global_options(tokens, error_type=CliUsageError)
        if not tokens:
            # phase1-F: bare `ccb` starts agents FRESH (no --continue / --resume).
            # Carrying over previous-session conversation across user-initiated
            # restarts caused ongoing-task pollution (e.g. an unrelated TD work
            # bled into the next user prompt). ccbd's own crash auto-recovery
            # uses CcbdStartPolicy.recovery_restore (hardcoded True) on a
            # separate code path, so this flip does not break recovery.
            return ParsedStartCommand(project=project, agent_names=(), restore=False, auto_permission=True)
        command = tokens[0]
        if command not in SUBCOMMANDS:
            return parse_start(tokens, project=project, error_type=CliUsageError)

        rest = tokens[1:]
        if command == 'ask':
            return parse_ask(
                rest,
                project=project,
                read_optional_stdin=self._read_optional_stdin,
                error_type=CliUsageError,
            )
        if command in WAIT_COMMAND_TO_MODE:
            return parse_wait(command, rest, project=project, error_type=CliUsageError)
        parser_fn = _COMMAND_PARSERS.get(command)
        if parser_fn is not None:
            return parser_fn(rest, project=project, error_type=CliUsageError)
        raise CliUsageError(f'unknown command: {command}')

    def _read_optional_stdin(self) -> str:
        if sys.stdin.isatty():
            return ''
        try:
            return read_stdin_text()
        except OSError:
            return ''
