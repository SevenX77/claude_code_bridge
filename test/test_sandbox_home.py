from __future__ import annotations

from pathlib import Path

from launcher.sandbox_home import (
    PROVIDER_AUTH_WHITELIST,
    WHITELIST,
    materialize_sandbox_home,
    whitelist_symlinks,
)


def test_whitelist_symlinks_combines_top_level_and_provider_auth() -> None:
    expected = list(WHITELIST) + list(PROVIDER_AUTH_WHITELIST)
    assert whitelist_symlinks() == expected


def test_materialize_sandbox_home_creates_top_level_and_nested_symlinks(monkeypatch, tmp_path: Path) -> None:
    home_root = tmp_path / 'home'
    cache_root = tmp_path / 'cache'
    (home_root / '.ssh').mkdir(parents=True)
    (home_root / '.gitconfig').write_text('[user]\nname = test\n', encoding='utf-8')

    # Set up provider auth files
    (home_root / '.claude.json').write_text('{"trust":"yes"}\n', encoding='utf-8')
    (home_root / '.claude').mkdir(parents=True)
    (home_root / '.claude' / '.credentials.json').write_text('{"token":"claude-tok"}\n', encoding='utf-8')
    (home_root / '.codex').mkdir(parents=True)
    (home_root / '.codex' / 'auth.json').write_text('{"token":"codex-tok"}\n', encoding='utf-8')
    (home_root / '.codex' / 'installation_id').write_text('codex-id\n', encoding='utf-8')
    (home_root / '.gemini').mkdir(parents=True)
    (home_root / '.gemini' / 'oauth_creds.json').write_text('{"token":"gem-tok"}\n', encoding='utf-8')
    (home_root / '.gemini' / 'google_accounts.json').write_text('{"acct":"x"}\n', encoding='utf-8')
    (home_root / '.gemini' / 'installation_id').write_text('gem-id\n', encoding='utf-8')

    monkeypatch.setenv('HOME', str(home_root))
    monkeypatch.setenv('XDG_CACHE_HOME', str(cache_root))

    sandbox_home = materialize_sandbox_home('abc123def456')

    assert sandbox_home == cache_root / 'ccb' / 'sandboxes' / 'abc123def456'

    # Top-level whitelist
    assert sandbox_home.joinpath('.ssh').is_symlink()
    assert sandbox_home.joinpath('.gitconfig').is_symlink()

    # Top-level Claude Code onboarding file
    assert sandbox_home.joinpath('.claude.json').is_symlink()
    assert sandbox_home.joinpath('.claude.json').resolve() == (home_root / '.claude.json').resolve()

    # Nested provider auth — symlinks must exist and resolve to real source
    assert sandbox_home.joinpath('.claude/.credentials.json').is_symlink()
    assert sandbox_home.joinpath('.claude/.credentials.json').resolve() == (home_root / '.claude' / '.credentials.json').resolve()
    assert sandbox_home.joinpath('.codex/auth.json').is_symlink()
    assert sandbox_home.joinpath('.codex/installation_id').is_symlink()
    assert sandbox_home.joinpath('.gemini/oauth_creds.json').is_symlink()
    assert sandbox_home.joinpath('.gemini/google_accounts.json').is_symlink()
    assert sandbox_home.joinpath('.gemini/installation_id').is_symlink()

    # Parent dirs exist (created by symlink target.parent.mkdir)
    assert sandbox_home.joinpath('.claude').is_dir()
    assert sandbox_home.joinpath('.codex').is_dir()
    assert sandbox_home.joinpath('.gemini').is_dir()


def test_materialize_sandbox_home_skips_missing_source_files(monkeypatch, tmp_path: Path) -> None:
    home_root = tmp_path / 'home'
    home_root.mkdir(parents=True)
    cache_root = tmp_path / 'cache'
    monkeypatch.setenv('HOME', str(home_root))
    monkeypatch.setenv('XDG_CACHE_HOME', str(cache_root))

    sandbox_home = materialize_sandbox_home('abc123def456')

    # No source files exist — sandbox is empty (no broken symlinks)
    assert sandbox_home.is_dir()
    assert not sandbox_home.joinpath('.ssh').exists()
    assert not sandbox_home.joinpath('.claude/.credentials.json').exists()
    assert not sandbox_home.joinpath('.gemini/oauth_creds.json').exists()


def test_materialize_sandbox_home_idempotent_on_repeat(monkeypatch, tmp_path: Path) -> None:
    home_root = tmp_path / 'home'
    cache_root = tmp_path / 'cache'
    (home_root / '.gemini').mkdir(parents=True)
    (home_root / '.gemini' / 'oauth_creds.json').write_text('{"token":"x"}\n', encoding='utf-8')
    monkeypatch.setenv('HOME', str(home_root))
    monkeypatch.setenv('XDG_CACHE_HOME', str(cache_root))

    first = materialize_sandbox_home('abc123def456')
    second = materialize_sandbox_home('abc123def456')

    assert first == second
    assert first.joinpath('.gemini/oauth_creds.json').is_symlink()
