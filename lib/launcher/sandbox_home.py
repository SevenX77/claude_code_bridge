from __future__ import annotations

import os
from pathlib import Path

from project.ids import compute_project_id


WHITELIST = ('.ssh', '.gitconfig', '.git-credentials', '.netrc')


def whitelist_symlinks() -> list[str]:
    return list(WHITELIST)


def materialize_sandbox_home(project_id: str) -> Path:
    home_root = _sandbox_root() / str(project_id).strip()[:12]
    home_root.mkdir(parents=True, exist_ok=True)
    source_home = Path.home().expanduser()
    for name in WHITELIST:
        source = source_home / name
        if not source.exists():
            continue
        target = home_root / name
        if target.is_symlink():
            try:
                if target.resolve() == source.resolve():
                    continue
            except Exception:
                pass
            try:
                target.unlink()
            except Exception:
                continue
        if target.exists():
            continue
        try:
            target.symlink_to(source, target_is_directory=source.is_dir())
        except Exception:
            continue
    return home_root


def sandbox_home_for_project_root(project_root: Path) -> Path:
    project_path = Path(project_root).expanduser().resolve()
    return materialize_sandbox_home(compute_project_id(project_path)[:12])


def sandbox_home_for_runtime_dir(runtime_dir: Path) -> Path:
    return sandbox_home_for_project_root(_project_root_from_runtime_dir(runtime_dir))


def _sandbox_root() -> Path:
    xdg_cache_home = str(os.environ.get('XDG_CACHE_HOME') or '').strip()
    if xdg_cache_home:
        return Path(xdg_cache_home).expanduser() / 'ccb' / 'sandboxes'
    return Path.home().expanduser() / '.cache' / 'ccb' / 'sandboxes'


def _project_root_from_runtime_dir(runtime_dir: Path) -> Path:
    candidate = Path(runtime_dir).expanduser().resolve()
    for parent in (candidate, *candidate.parents):
        if parent.name == '.ccb':
            return parent.parent
    return candidate.parent


__all__ = [
    'WHITELIST',
    'materialize_sandbox_home',
    'sandbox_home_for_project_root',
    'sandbox_home_for_runtime_dir',
    'whitelist_symlinks',
]
