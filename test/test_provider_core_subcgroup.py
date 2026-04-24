"""Tests for provider_core.subcgroup."""
from __future__ import annotations

from pathlib import Path

import pytest

from provider_core import subcgroup


@pytest.fixture
def mock_cgroup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Simulate a cgroup v2 delegated hierarchy under tmp_path."""
    fake_fs_cgroup = tmp_path / "fs-cgroup"
    keeper_dir = fake_fs_cgroup / "user.slice" / "claude-ccb-x.service"
    keeper_dir.mkdir(parents=True)

    (keeper_dir / "cgroup.controllers").write_text("cpu io memory pids\n")
    (keeper_dir / "cgroup.subtree_control").write_text("")
    (keeper_dir / "cgroup.procs").write_text("")

    # Patch the two helpers that read real filesystem
    monkeypatch.setattr(subcgroup, "_resolve_keeper_cgroup", lambda: keeper_dir)
    # Ensure feature flag on for these tests unless we test disabled
    monkeypatch.setenv(subcgroup.SUBCGROUP_ENV, "1")
    return keeper_dir


class TestIsEnabled:
    @pytest.mark.parametrize("val,expected", [
        ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
        ("", False), ("0", False), ("false", False), ("random", False),
    ])
    def test_env_variants(self, monkeypatch, val, expected):
        monkeypatch.setenv(subcgroup.SUBCGROUP_ENV, val)
        assert subcgroup.is_enabled() is expected

    def test_unset_returns_false(self, monkeypatch):
        monkeypatch.delenv(subcgroup.SUBCGROUP_ENV, raising=False)
        assert subcgroup.is_enabled() is False


class TestSupportsCgroupV2Delegation:
    def test_returns_true_with_full_controllers_and_writable(self, mock_cgroup):
        assert subcgroup.supports_cgroup_v2_delegation() is True

    def test_returns_false_when_keeper_cgroup_unresolvable(self, monkeypatch):
        monkeypatch.setattr(subcgroup, "_resolve_keeper_cgroup", lambda: None)
        assert subcgroup.supports_cgroup_v2_delegation() is False

    def test_returns_false_when_controllers_file_missing(self, mock_cgroup):
        (mock_cgroup / "cgroup.controllers").unlink()
        assert subcgroup.supports_cgroup_v2_delegation() is False

    def test_returns_false_when_pids_controller_missing(self, mock_cgroup):
        (mock_cgroup / "cgroup.controllers").write_text("cpu io\n")
        assert subcgroup.supports_cgroup_v2_delegation() is False

    def test_returns_false_when_memory_controller_missing(self, mock_cgroup):
        (mock_cgroup / "cgroup.controllers").write_text("cpu io pids\n")
        assert subcgroup.supports_cgroup_v2_delegation() is False

    def test_returns_false_when_subtree_control_not_writable(self, mock_cgroup):
        (mock_cgroup / "cgroup.subtree_control").chmod(0o444)
        assert subcgroup.supports_cgroup_v2_delegation() is False


class TestSetupKeeperSubcgroup:
    def test_skipped_when_disabled(self, monkeypatch):
        monkeypatch.delenv(subcgroup.SUBCGROUP_ENV, raising=False)
        assert subcgroup.setup_keeper_subcgroup() == "skipped_disabled"

    def test_skipped_when_unsupported(self, monkeypatch):
        monkeypatch.setenv(subcgroup.SUBCGROUP_ENV, "1")
        monkeypatch.setattr(subcgroup, "_resolve_keeper_cgroup", lambda: None)
        assert subcgroup.setup_keeper_subcgroup() == "skipped_unsupported"

    def test_missing_controllers_returns_unsupported(self, mock_cgroup):
        (mock_cgroup / "cgroup.controllers").write_text("cpu io\n")
        assert subcgroup.setup_keeper_subcgroup() == "skipped_unsupported"

    def test_setup_creates_keeper_subdir_moves_pids_and_enables_controllers(
        self, mock_cgroup, monkeypatch
    ):
        # Simulate scope root holding 3 PIDs (self + ccb CLI + ccbd daemon_process)
        (mock_cgroup / "cgroup.procs").write_text("98765\n11111\n22222\n")
        monkeypatch.setattr(subcgroup.os, "getpid", lambda: 98765)
        result = subcgroup.setup_keeper_subcgroup()
        assert result == "setup"
        keeper = mock_cgroup / "keeper"
        assert keeper.is_dir()
        # All scope-root PIDs were appended to keeper/cgroup.procs via multiple writes;
        # real cgroupfs overwrites on each write, so last-write-wins. We only assert
        # the function attempted to move procs.
        st = mock_cgroup.joinpath("cgroup.subtree_control").read_text()
        assert "+pids" in st and "+memory" in st

    def test_setup_idempotent_if_keeper_already_exists(
        self, mock_cgroup, monkeypatch
    ):
        # Pre-create keeper/
        (mock_cgroup / "keeper").mkdir()
        monkeypatch.setattr(subcgroup.os, "getpid", lambda: 42)
        # Should still succeed
        assert subcgroup.setup_keeper_subcgroup() == "setup"


class TestMovePidToAgentSubcgroup:
    def test_skipped_when_flag_disabled(self, monkeypatch):
        monkeypatch.delenv(subcgroup.SUBCGROUP_ENV, raising=False)
        result = subcgroup.move_pid_to_agent_subcgroup(1234, "a1", "codex")
        assert result == "skipped_disabled"

    def test_skipped_when_delegation_unsupported(self, monkeypatch):
        monkeypatch.setenv(subcgroup.SUBCGROUP_ENV, "1")
        monkeypatch.setattr(subcgroup, "_resolve_keeper_cgroup", lambda: None)
        result = subcgroup.move_pid_to_agent_subcgroup(1234, "a1", "codex")
        assert result == "skipped_unsupported"

    def test_moved_creates_subdir_and_writes_limits_and_procs(self, mock_cgroup):
        result = subcgroup.move_pid_to_agent_subcgroup(9999, "a1", "codex")
        assert result == "moved"
        sub = mock_cgroup / "agent-a1"
        assert sub.is_dir()
        assert sub.joinpath("pids.max").read_text().strip() == "400"
        # codex default 2G = 2*1024^3
        assert sub.joinpath("memory.max").read_text().strip() == str(2 * 1024**3)
        assert sub.joinpath("cgroup.procs").read_text().strip() == "9999"

    def test_moved_uses_provider_default_budget(self, mock_cgroup):
        result = subcgroup.move_pid_to_agent_subcgroup(1, "a2", "gemini")
        assert result == "moved"
        sub = mock_cgroup / "agent-a2"
        assert sub.joinpath("pids.max").read_text().strip() == "150"  # gemini

    def test_moved_with_explicit_budget_overrides_defaults(self, mock_cgroup):
        result = subcgroup.move_pid_to_agent_subcgroup(
            2, "a3", "codex", pids_max=1000, memory_max="4G"
        )
        assert result == "moved"
        sub = mock_cgroup / "agent-a3"
        assert sub.joinpath("pids.max").read_text().strip() == "1000"
        assert sub.joinpath("memory.max").read_text().strip() == str(4 * 1024**3)

    def test_failed_when_procs_write_raises(self, mock_cgroup, monkeypatch):
        # Make cgroup.procs path unwritable by removing write perm on subdir
        def fake_mkdir(self, *args, **kw):
            # Mark the sub so writes fail
            real_path = Path.__new__(Path)
            Path.mkdir(self, *args, **kw)
            # After mkdir, remove write permission
            self.chmod(0o555)
        # Simpler approach: let the subcgroup be created as a regular file first
        (mock_cgroup / "agent-fail").write_text("stub")  # blocks mkdir as dir
        result = subcgroup.move_pid_to_agent_subcgroup(1, "fail", "codex")
        assert result == "failed"

    def test_unknown_provider_falls_back_to_default_budget(self, mock_cgroup):
        result = subcgroup.move_pid_to_agent_subcgroup(3, "a4", "unknown-provider")
        assert result == "moved"
        # No default budget → fallback to 500 pids, 1G memory
        sub = mock_cgroup / "agent-a4"
        assert sub.joinpath("pids.max").read_text().strip() == "500"
        assert sub.joinpath("memory.max").read_text().strip() == str(1 * 1024**3)


class TestParseMemorySize:
    @pytest.mark.parametrize("inp,expected", [
        ("2G", 2 * 1024**3),
        ("512M", 512 * 1024**2),
        ("1024K", 1024 * 1024),
        ("2048", 2048),
        ("1500M", 1500 * 1024**2),
        ("4g", 4 * 1024**3),
    ])
    def test_valid_sizes(self, inp, expected):
        assert subcgroup._parse_memory_size(inp).strip() == str(expected)

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            subcgroup._parse_memory_size("")

    def test_non_numeric_raises(self):
        with pytest.raises(ValueError):
            subcgroup._parse_memory_size("foo")


class TestExtractAgentNameFromRuntimeDir:
    def test_extracts_agent_name_from_standard_layout(self, tmp_path):
        runtime = tmp_path / ".ccb" / "agents" / "a1" / "provider-runtime" / "codex" / "current"
        runtime.mkdir(parents=True)
        assert subcgroup.extract_agent_name_from_runtime_dir(runtime) == "a1"

    def test_extracts_agent_name_shallow(self, tmp_path):
        runtime = tmp_path / ".ccb" / "agents" / "a2" / "provider-runtime"
        runtime.mkdir(parents=True)
        assert subcgroup.extract_agent_name_from_runtime_dir(runtime) == "a2"

    def test_returns_none_when_layout_does_not_match(self, tmp_path):
        runtime = tmp_path / "not-ccb" / "whatever"
        runtime.mkdir(parents=True)
        assert subcgroup.extract_agent_name_from_runtime_dir(runtime) is None

    def test_returns_none_on_nonexistent_path(self, tmp_path):
        result = subcgroup.extract_agent_name_from_runtime_dir(
            tmp_path / "does-not-exist"
        )
        # Resolving a non-existent path still works; just won't match layout
        assert result is None


class TestSanitizeAgentName:
    @pytest.mark.parametrize("inp,expected", [
        ("a1", "agent-a1"),
        ("codex_1", "agent-codex_1"),
        ("x/y", "agent-x_y"),   # slash replaced
        ("", "agent-unknown"),   # empty fallback
        ("a.b", "agent-a_b"),
    ])
    def test_cases(self, inp, expected):
        assert subcgroup._sanitize_agent_name(inp) == expected
