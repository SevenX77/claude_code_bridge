from __future__ import annotations

import os
import time

from agents.config_identity import project_config_identity_payload
from agents.config_loader import load_project_config
from ccbd.socket_client import CcbdClient, CcbdClientError
from cli.master_claude_identity import find_master_claude_pid, is_pid_alive, read_owner_lockfile

from .models import CcbdServiceError, DaemonHandle


def daemon_matches_project_config(context, client) -> bool:
    expected = project_config_identity_payload(
        load_project_config(context.project.project_root).config
    )
    payload = client.ping('ccbd')
    actual_signature = str(payload.get('config_signature') or '').strip()
    if actual_signature:
        return actual_signature == expected['config_signature']
    known_agents = payload.get('known_agents')
    if not isinstance(known_agents, list):
        return False
    actual_agents = tuple(
        str(item).strip().lower() for item in known_agents if str(item).strip()
    )
    return actual_agents == tuple(expected['known_agents'])


def connect_compatible_daemon(
    context,
    inspection,
    *,
    restart_on_mismatch: bool,
    client_factory=CcbdClient,
    daemon_matches_project_config_fn=daemon_matches_project_config,
    shutdown_incompatible_daemon_fn=None,
) -> DaemonHandle | None:
    if not inspection.socket_connectable:
        return None
    _enforce_master_claude_owner(context)
    client = client_factory(context.paths.ccbd_socket_path)
    try:
        matches_config = daemon_matches_project_config_fn(context, client)
    except CcbdClientError:
        # A transient ping failure is not evidence of config drift.
        return DaemonHandle(client=client, inspection=inspection, started=False)
    if matches_config:
        return DaemonHandle(client=client, inspection=inspection, started=False)
    if not restart_on_mismatch:
        return None
    if shutdown_incompatible_daemon_fn is None:
        raise ValueError('shutdown_incompatible_daemon_fn is required when restart_on_mismatch')
    shutdown_incompatible_daemon_fn(context, client)
    return None


def shutdown_incompatible_daemon(
    context,
    client,
    *,
    inspect_daemon_fn,
    incompatible_daemon_error: str,
    shutdown_timeout_s: float,
    unavailable_health_states,
) -> None:
    try:
        client.stop_all(force=False)
    except CcbdClientError:
        pass
    deadline = time.time() + shutdown_timeout_s
    while time.time() < deadline:
        _, _, inspection = inspect_daemon_fn(context)
        if (
            not inspection.socket_connectable
            or inspection.health in unavailable_health_states
        ):
            return
        time.sleep(0.05)
    raise CcbdServiceError(
        f'{incompatible_daemon_error}; old ccbd did not shut down in time'
    )


def _enforce_master_claude_owner(context) -> None:
    path = context.paths.ccbd_owner_lockfile_path
    owner = read_owner_lockfile(path)
    if owner is None:
        return
    if not is_pid_alive(owner.pid):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    current_pid = find_master_claude_pid(os.getpid())
    if current_pid is None or current_pid == owner.pid:
        return
    raise CcbdServiceError(
        f'[CCB Fatal] Project {context.project.project_root} is currently locked by master Claude PID {owner.pid}.\n'
        f'Your master Claude PID {current_pid} cannot share this ccbd (would corrupt agent conversation context).\n'
        'Phase 1 mitigation: only one master Claude per project at a time. Open another project or kill the other Claude first.'
    )


__all__ = [
    'connect_compatible_daemon',
    'daemon_matches_project_config',
    'shutdown_incompatible_daemon',
]
