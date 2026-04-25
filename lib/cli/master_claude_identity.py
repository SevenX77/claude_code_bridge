from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OwnerInfo:
    pid: int
    started_at: str | None = None


@dataclass(frozen=True)
class _ProcStat:
    pid: int
    comm: str
    ppid: int


def find_master_claude_pid(start_pid: int) -> int | None:
    current_pid = int(start_pid)
    fallback_pid: int | None = None
    while current_pid > 0:
        stat = _read_proc_stat(current_pid)
        if stat is None:
            break
        if fallback_pid is None and stat.ppid > 0:
            fallback_pid = stat.ppid
        if stat.comm == 'claude':
            return current_pid
        if stat.ppid <= 0 or stat.ppid == current_pid:
            break
        current_pid = stat.ppid
    return fallback_pid


def read_owner_lockfile(path: Path) -> OwnerInfo | None:
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except FileNotFoundError:
        return None
    except Exception:
        return None
    payload: dict[str, str] = {}
    for line in lines:
        key, sep, value = line.partition('=')
        if not sep:
            continue
        payload[key.strip()] = value.strip()
    raw_pid = payload.get('pid', '')
    if not raw_pid.isdigit():
        return None
    pid = int(raw_pid)
    if pid <= 0:
        return None
    return OwnerInfo(pid=pid, started_at=payload.get('started_at') or None)


def is_pid_alive(pid: int) -> bool:
    return _read_proc_stat(pid) is not None


def _read_proc_stat(pid: int) -> _ProcStat | None:
    try:
        raw = Path(f'/proc/{int(pid)}/stat').read_text(encoding='utf-8')
    except Exception:
        return None
    start = raw.find('(')
    end = raw.rfind(')')
    if start < 0 or end <= start:
        return None
    prefix = raw[:start].strip()
    suffix = raw[end + 1 :].strip().split()
    if not prefix.isdigit() or len(suffix) < 2:
        return None
    try:
        return _ProcStat(
            pid=int(prefix),
            comm=raw[start + 1 : end],
            ppid=int(suffix[1]),
        )
    except Exception:
        return None


__all__ = ['OwnerInfo', 'find_master_claude_pid', 'is_pid_alive', 'read_owner_lockfile']
