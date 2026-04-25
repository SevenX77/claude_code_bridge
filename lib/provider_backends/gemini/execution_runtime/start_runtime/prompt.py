from __future__ import annotations

from pathlib import Path

from provider_execution.common import send_prompt_to_runtime_target


def send_prompt(backend: object, pane_id: str, text: str) -> None:
    send_prompt_to_runtime_target(backend, pane_id, text)


def write_request_file(*, session, req_id: str, message: str) -> Path:
    """Write the prompt body to a Markdown file Gemini will read via @path.

    Targets ``~/.cache/ccb/gemini-requests/`` — outside the project tree.

    Reason: Gemini CLI 0.39+ honors .gitignore for ReadFile/``@path``, and
    many user projects (including those CCB itself just installed into)
    have ``.ccb-requests/`` AND ``.ccb/`` listed in .gitignore — that
    triggers "File path ... is ignored by configured ignore patterns" →
    4× retry → "A potential loop was detected" dialog → agent dead.

    Both the legacy ``<work_dir>/.ccb-requests/`` and the per-agent
    ``<work_dir>/.ccb/agents/<name>/provider-runtime/gemini/`` paths are
    inside the project tree, so they hit the same .gitignore. A truly
    out-of-project location (under ``~/.cache``) makes the .gitignore
    walk-up chain go: ``~/.cache/ccb/gemini-requests/`` → ``~/.cache/`` →
    ``~/`` — never crossing work_dir's .gitignore.

    Why ``~/.cache`` (XDG_CACHE_HOME spirit) and not ``/tmp``: cache is
    user-scoped (right ownership for OAuth-style credentials some hooks
    may inject into the prompt body), persists long enough that an
    in-flight prompt isn't tmpwatch'd mid-read, and survives reboots
    only briefly so stale prompts don't accumulate forever.
    """
    cache_root = Path.home() / '.cache' / 'ccb' / 'gemini-requests'
    cache_root.mkdir(parents=True, exist_ok=True)
    request_path = cache_root / f'{req_id}.md'
    request_path.write_text(str(message or ''), encoding='utf-8')
    return request_path


def build_exact_prompt(*, session, req_id: str, message: str) -> str:
    request_path = write_request_file(session=session, req_id=req_id, message=message)
    return f'CCB_REQ_ID: {req_id} Execute the full request from @{request_path} and reply directly.'


__all__ = ['build_exact_prompt', 'send_prompt', 'write_request_file']
