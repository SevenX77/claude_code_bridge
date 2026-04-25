from __future__ import annotations

from pathlib import Path

from cli.master_claude_identity import OwnerInfo, find_master_claude_pid, is_pid_alive, read_owner_lockfile


def test_read_owner_lockfile_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / 'ccbd.owner'
    path.write_text('pid=1067266\nstarted_at=2026-04-25T00:00:00Z\n', encoding='utf-8')

    assert read_owner_lockfile(path) == OwnerInfo(pid=1067266, started_at='2026-04-25T00:00:00Z')


def test_find_master_claude_pid_prefers_nearest_claude_ancestor(monkeypatch) -> None:
    mapping = {
        300: ('python', 200),
        200: ('bash', 150),
        150: ('claude', 120),
        120: ('claude', 1),
    }

    monkeypatch.setattr(
        'cli.master_claude_identity._read_proc_stat',
        lambda pid: type('Proc', (), {'pid': pid, 'comm': mapping[pid][0], 'ppid': mapping[pid][1]}) if pid in mapping else None,
    )

    assert find_master_claude_pid(300) == 150


def test_find_master_claude_pid_falls_back_to_parent_when_no_claude(monkeypatch) -> None:
    mapping = {
        500: ('python', 400),
        400: ('bash', 1),
    }

    monkeypatch.setattr(
        'cli.master_claude_identity._read_proc_stat',
        lambda pid: type('Proc', (), {'pid': pid, 'comm': mapping[pid][0], 'ppid': mapping[pid][1]}) if pid in mapping else None,
    )

    assert find_master_claude_pid(500) == 400


def test_is_pid_alive_delegates_to_proc_stat(monkeypatch) -> None:
    monkeypatch.setattr('cli.master_claude_identity._read_proc_stat', lambda pid: object() if pid == 77 else None)

    assert is_pid_alive(77) is True
    assert is_pid_alive(88) is False
