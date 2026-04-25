from __future__ import annotations

import os
from pathlib import Path

from project.ids import compute_project_id


WHITELIST = ('.ssh', '.gitconfig', '.git-credentials', '.netrc')

# Provider auth/identity files. Symlinked so subscription logins are
# shared across all CCB agents in all projects (user wants one OAuth =
# all agents authenticated, not per-project re-login).
#
# IMPORTANT: only files that providers READ (or only refresh in-place
# atomically). Files that the launcher synthesizes/projects per agent
# (e.g. .codex/config.toml, .gemini/settings.json, .gemini/trustedFolders.json)
# are NOT whitelisted — those go through provider-specific projection
# in launcher_runtime/home.py. Symlinking them would let an agent's
# launcher overwrite the user's real config.
PROVIDER_AUTH_WHITELIST = (
    '.claude/.credentials.json',
    '.codex/auth.json',
    '.codex/installation_id',
    '.gemini/oauth_creds.json',
    '.gemini/google_accounts.json',
    '.gemini/installation_id',
)


def whitelist_symlinks() -> list[str]:
    return list(WHITELIST) + list(PROVIDER_AUTH_WHITELIST)


def materialize_sandbox_home(project_id: str) -> Path:
    home_root = _sandbox_root() / str(project_id).strip()[:12]
    home_root.mkdir(parents=True, exist_ok=True)
    source_home = Path.home().expanduser()
    for name in WHITELIST:
        _link_into_sandbox(source_home, home_root, name)
    for nested in PROVIDER_AUTH_WHITELIST:
        _link_into_sandbox(source_home, home_root, nested)
    return home_root


def _link_into_sandbox(source_home: Path, home_root: Path, relative: str) -> None:
    source = source_home / relative
    if not source.exists():
        return
    target = home_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        try:
            if target.resolve() == source.resolve():
                return
        except Exception:
            pass
        try:
            target.unlink()
        except Exception:
            return
    elif target.exists():
        # Existing real file/dir at this location — don't overwrite.
        # This can happen when an agent CLI created its own state dir
        # before the symlink was set up. Leave it alone.
        return
    try:
        target.symlink_to(source, target_is_directory=source.is_dir())
    except Exception:
        return


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
    'PROVIDER_AUTH_WHITELIST',
    'WHITELIST',
    'materialize_sandbox_home',
    'sandbox_home_for_project_root',
    'sandbox_home_for_runtime_dir',
    'whitelist_symlinks',
]
