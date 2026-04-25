from __future__ import annotations

from pathlib import Path

from launcher.sandbox_home import WHITELIST, materialize_sandbox_home, whitelist_symlinks


def test_whitelist_symlinks_matches_phase1_allowlist() -> None:
    assert whitelist_symlinks() == list(WHITELIST)


def test_materialize_sandbox_home_creates_project_root_and_whitelist_symlinks(monkeypatch, tmp_path: Path) -> None:
    home_root = tmp_path / 'home'
    cache_root = tmp_path / 'cache'
    (home_root / '.ssh').mkdir(parents=True)
    (home_root / '.gitconfig').write_text('[user]\nname = test\n', encoding='utf-8')
    monkeypatch.setenv('HOME', str(home_root))
    monkeypatch.setenv('XDG_CACHE_HOME', str(cache_root))

    sandbox_home = materialize_sandbox_home('abc123def456')

    assert sandbox_home == cache_root / 'ccb' / 'sandboxes' / 'abc123def456'
    assert sandbox_home.is_dir()
    assert sandbox_home.joinpath('.ssh').is_symlink()
    assert sandbox_home.joinpath('.gitconfig').is_symlink()
    assert sandbox_home.joinpath('.claude').exists() is False
    assert sandbox_home.joinpath('.codex').exists() is False
    assert sandbox_home.joinpath('.gemini').exists() is False
