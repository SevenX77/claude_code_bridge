from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from ccbd.models import LeaseHealth
import cli.services.daemon as daemon_service
from cli.services.daemon_runtime import compat as daemon_compat
import pytest


def _context(project_root: Path):
    project_root.mkdir(parents=True, exist_ok=True)
    lockfile = project_root / '.ccb' / 'ccbd' / 'ccbd.owner'
    socket_path = project_root / '.ccb' / 'ccbd' / 'ccbd.sock'
    return SimpleNamespace(
        project=SimpleNamespace(project_root=project_root, project_id='project-1'),
        paths=SimpleNamespace(
            ccbd_owner_lockfile_path=lockfile,
            ccbd_socket_path=socket_path,
        ),
    )


def test_connect_compatible_daemon_rejects_different_master_pid(monkeypatch, tmp_path: Path) -> None:
    ctx = _context(tmp_path / 'repo-lock')
    ctx.paths.ccbd_owner_lockfile_path.parent.mkdir(parents=True, exist_ok=True)
    ctx.paths.ccbd_owner_lockfile_path.write_text(
        'pid=1067266\nstarted_at=2026-04-25T00:00:00Z\n',
        encoding='utf-8',
    )
    inspection = SimpleNamespace(socket_connectable=True)

    monkeypatch.setattr('cli.services.daemon_runtime.compat.is_pid_alive', lambda pid: True)
    monkeypatch.setattr('cli.services.daemon_runtime.compat.find_master_claude_pid', lambda start_pid: 3892212)

    with pytest.raises(daemon_service.CcbdServiceError) as excinfo:
        daemon_compat.connect_compatible_daemon(
            ctx,
            inspection,
            restart_on_mismatch=False,
            client_factory=lambda socket_path: None,
            daemon_matches_project_config_fn=lambda context, client: True,
        )

    assert str(excinfo.value) == (
        f'[CCB Fatal] Project {ctx.project.project_root} is currently locked by master Claude PID 1067266.\n'
        'Your master Claude PID 3892212 cannot share this ccbd (would corrupt agent conversation context).\n'
        'Phase 1 mitigation: only one master Claude per project at a time. Open another project or kill the other Claude first.'
    )


def test_connect_compatible_daemon_removes_dead_owner_lock_and_continues(monkeypatch, tmp_path: Path) -> None:
    ctx = _context(tmp_path / 'repo-stale-owner')
    ctx.paths.ccbd_owner_lockfile_path.parent.mkdir(parents=True, exist_ok=True)
    ctx.paths.ccbd_owner_lockfile_path.write_text(
        'pid=555\nstarted_at=2026-04-25T00:00:00Z\n',
        encoding='utf-8',
    )
    inspection = SimpleNamespace(socket_connectable=True)

    monkeypatch.setattr('cli.services.daemon_runtime.compat.is_pid_alive', lambda pid: False)

    handle = daemon_compat.connect_compatible_daemon(
        ctx,
        inspection,
        restart_on_mismatch=False,
        client_factory=lambda socket_path: SimpleNamespace(socket_path=socket_path),
        daemon_matches_project_config_fn=lambda context, client: True,
    )

    assert handle is not None
    assert ctx.paths.ccbd_owner_lockfile_path.exists() is False


def test_ensure_daemon_started_cleans_owner_lockfile_for_stale_daemon(monkeypatch, tmp_path: Path) -> None:
    ctx = _context(tmp_path / 'repo-stale-daemon')
    ctx.paths.ccbd_owner_lockfile_path.parent.mkdir(parents=True, exist_ok=True)
    ctx.paths.ccbd_owner_lockfile_path.write_text(
        'pid=777\nstarted_at=2026-04-25T00:00:00Z\n',
        encoding='utf-8',
    )
    inspection = SimpleNamespace(
        socket_connectable=False,
        health=LeaseHealth.STALE,
        pid_alive=False,
    )
    sentinel = object()

    monkeypatch.setattr(daemon_service, 'inspect_daemon', lambda context: (None, None, inspection))
    monkeypatch.setattr(daemon_service, '_ensure_daemon_started_runtime', lambda *args, **kwargs: sentinel)

    assert daemon_service.ensure_daemon_started(ctx) is sentinel
    assert ctx.paths.ccbd_owner_lockfile_path.exists() is False
