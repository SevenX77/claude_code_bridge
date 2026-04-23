from __future__ import annotations

from pathlib import Path
import shlex


def build_hook_command(
    *,
    provider: str,
    script_path: Path,
    python_executable: str,
    completion_dir: Path,
    agent_name: str,
    workspace_path: Path,
    event: str = 'finish',
    reception_dir: Path | None = None,
    timeout_s: int | None = None,
) -> str:
    parts = [
        python_executable,
        str(Path(script_path).expanduser()),
        '--provider',
        str(provider),
        '--event',
        str(event),
        '--completion-dir',
        str(Path(completion_dir).expanduser()),
        '--agent-name',
        str(agent_name),
        '--workspace',
        str(Path(workspace_path).expanduser()),
    ]
    if reception_dir is not None:
        parts.extend(['--reception-dir', str(Path(reception_dir).expanduser())])
    quoted = ' '.join(shlex.quote(str(part)) for part in parts)
    if timeout_s is not None and timeout_s > 0:
        return f'/usr/bin/timeout {int(timeout_s)} {quoted}'
    return quoted


__all__ = ['build_hook_command']
