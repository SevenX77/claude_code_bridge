from __future__ import annotations

import logging
from pathlib import Path

from provider_core.subcgroup import (
    is_enabled as subcgroup_is_enabled,
    move_pid_to_agent_subcgroup,
)
from terminal_runtime.tmux_identity import apply_ccb_pane_identity

from .tmux_backend import prepared_state, run_cwd, tmux_backend
from .tmux_panes import (
    best_effort_kill_tmux_pane,
    create_detached_tmux_pane,
    launch_pane,
    pane_meets_minimum_size,
    prepare_detached_tmux_server,
)

_logger = logging.getLogger(__name__)


def _best_effort_migrate_agent_subcgroup(backend, pane_id: str, spec) -> None:
    """Move the agent's tmux pane process into a per-agent cgroup v2 sub-dir.

    Feature-flagged via CCB_PER_AGENT_SUBCGROUP=1; harmless no-op when off
    or when the keeper scope lacks cgroup v2 delegation. Never raises -
    any failure is logged and swallowed so the agent launch itself is
    unaffected.
    """
    if not subcgroup_is_enabled():
        return
    try:
        result = backend._tmux_run(  # type: ignore[attr-defined]
            ['display-message', '-p', '-t', pane_id, '#{pane_pid}'],
            capture=True,
            timeout=1.0,
        )
    except Exception as e:  # noqa: BLE001
        _logger.warning("subcgroup: display-message failed for %s: %s", pane_id, e)
        return
    pane_pid = (result.stdout or '').strip()
    if not pane_pid.isdigit():
        _logger.warning("subcgroup: pane_pid not numeric for %s: %r", pane_id, pane_pid)
        return
    try:
        outcome = move_pid_to_agent_subcgroup(int(pane_pid), spec.name, spec.provider)
    except Exception as e:  # noqa: BLE001
        _logger.warning("subcgroup: move_pid_to_agent_subcgroup raised: %s", e)
        return
    _logger.info(
        "subcgroup: agent=%s provider=%s pid=%s outcome=%s",
        spec.name, spec.provider, pane_pid, outcome,
    )


def launch_tmux_runtime(
    context,
    command,
    spec,
    plan,
    launcher,
    *,
    backend_factory,
    pane_title_marker_fn,
    launch_session_id_fn,
    create_detached_tmux_pane_fn,
    pane_meets_minimum_size_fn,
    best_effort_kill_tmux_pane_fn,
    write_session_file_fn,
    assigned_pane_id: str | None = None,
    style_index: int = 0,
    tmux_socket_path: str | None = None,
    allow_detached_fallback: bool = True,
) -> None:
    runtime_dir = context.paths.agent_dir(spec.name) / 'provider-runtime' / spec.provider
    runtime_dir.mkdir(parents=True, exist_ok=True)
    launch_session_id = launch_session_id_fn(spec.name)
    prepared = prepared_state(launcher, runtime_dir)
    backend = tmux_backend(backend_factory, tmux_socket_path)
    pane_title_marker = pane_title_marker_fn(context, spec)
    start_cmd = launcher.build_start_cmd(command, spec, runtime_dir, launch_session_id)
    runtime_cwd = run_cwd(
        launcher,
        command=command,
        spec=spec,
        plan=plan,
        runtime_dir=runtime_dir,
        launch_session_id=launch_session_id,
    )
    pane_id = launch_pane(
        backend,
        spec_name=spec.name,
        assigned_pane_id=assigned_pane_id,
        start_cmd=start_cmd,
        run_cwd=runtime_cwd,
        create_detached_tmux_pane_fn=create_detached_tmux_pane_fn,
        pane_meets_minimum_size_fn=pane_meets_minimum_size_fn,
        best_effort_kill_tmux_pane_fn=best_effort_kill_tmux_pane_fn,
        allow_detached_fallback=allow_detached_fallback,
    )
    _best_effort_migrate_agent_subcgroup(backend, pane_id, spec)
    apply_ccb_pane_identity(
        backend,
        pane_id,
        title=spec.name,
        agent_label=spec.name,
        project_id=context.project.project_id,
        order_index=style_index,
        slot_key=spec.name,
    )

    provider_payload = launcher.build_session_payload(
        context=context,
        spec=spec,
        plan=plan,
        runtime_dir=runtime_dir,
        run_cwd=runtime_cwd,
        pane_id=pane_id,
        pane_title_marker=pane_title_marker,
        start_cmd=start_cmd,
        launch_session_id=launch_session_id,
        prepared_state=prepared,
    )
    write_session_file_fn(
        context=context,
        spec=spec,
        plan=plan,
        runtime_dir=runtime_dir,
        run_cwd=runtime_cwd,
        pane_id=pane_id,
        tmux_socket_name=str(getattr(backend, '_socket_name', '') or '').strip() or None,
        tmux_socket_path=str(getattr(backend, '_socket_path', '') or '').strip() or None,
        pane_title_marker=pane_title_marker,
        start_cmd=start_cmd,
        launch_session_id=launch_session_id,
        provider_payload=provider_payload,
    )
    if launcher.post_launch is not None:
        launcher.post_launch(
            backend,
            pane_id,
            runtime_dir,
            launch_session_id,
            prepared,
        )


__all__ = [
    'best_effort_kill_tmux_pane',
    'create_detached_tmux_pane',
    'launch_tmux_runtime',
    'pane_meets_minimum_size',
    'prepare_detached_tmux_server',
]
