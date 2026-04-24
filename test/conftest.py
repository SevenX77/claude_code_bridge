from __future__ import annotations

import glob
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

repo_root = Path(__file__).resolve().parents[1]
lib_dir = repo_root / "lib"
if str(lib_dir) not in sys.path:
    sys.path.insert(0, str(lib_dir))

import project.resolver as project_resolver_module


def pytest_configure() -> None:
    if str(lib_dir) not in sys.path:
        sys.path.insert(0, str(lib_dir))


@pytest.fixture(autouse=True)
def _ignore_host_level_tmp_anchor(monkeypatch, tmp_path_factory) -> None:
    original = project_resolver_module.find_parent_project_anchor_dir
    pytest_tmp_root = tmp_path_factory.getbasetemp().resolve()

    def _patched(path: Path):
        result = original(path)
        if result is None:
            return None
        anchor_root = result.parent.resolve()
        if pytest_tmp_root.is_relative_to(anchor_root) and not anchor_root.is_relative_to(pytest_tmp_root):
            return None
        return result

    monkeypatch.setattr(project_resolver_module, 'find_parent_project_anchor_dir', _patched)


# ============================================================
# CCB tmux daemon leak cleanup.
# Tests in test_v2_phase2_entrypoint.py spawn `ccb` via subprocess.
# Those subprocesses start the CCB keeper which wraps itself in an
# independent tmux daemon (socket under tmp_path/.ccb/ccbd/tmux.sock
# or /run/user/$UID/ccb-runtime/tmux-*.sock). The test's in-process
# app.shutdown() cannot reach those daemons.
# ============================================================

_leak_logger = logging.getLogger(__name__)


def _safe_kill_tmux_server(sock: str) -> None:
    try:
        result = subprocess.run(
            ['tmux', '-S', sock, 'kill-server'],
            timeout=5, check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            _leak_logger.warning(
                "cleanup: tmux kill-server %s returned rc=%d", sock, result.returncode
            )
    except FileNotFoundError:
        return
    except subprocess.TimeoutExpired:
        _leak_logger.warning("cleanup: tmux kill-server %s timed out after 5s", sock)


def _runtime_socket_pattern() -> str:
    return f'/run/user/{os.getuid()}/ccb-runtime/tmux-*.sock'


@pytest.fixture(autouse=True)
def _cleanup_ccb_tmux_per_test(tmp_path):
    before = set(glob.glob(_runtime_socket_pattern()))
    try:
        yield
    finally:
        for sock_path in tmp_path.rglob('.ccb/ccbd/tmux.sock'):
            _safe_kill_tmux_server(str(sock_path))
        after = set(glob.glob(_runtime_socket_pattern()))
        for sock in after - before:
            _safe_kill_tmux_server(sock)


@pytest.fixture(autouse=True, scope='session')
def _cleanup_ccb_tmux_session_end(tmp_path_factory):
    before = set(glob.glob(_runtime_socket_pattern()))
    try:
        yield
    finally:
        base_tmp = tmp_path_factory.getbasetemp()
        for sock_path in base_tmp.rglob('.ccb/ccbd/tmux.sock'):
            _safe_kill_tmux_server(str(sock_path))
        after = set(glob.glob(_runtime_socket_pattern()))
        for sock in after - before:
            _safe_kill_tmux_server(sock)
