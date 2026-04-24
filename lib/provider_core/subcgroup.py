"""Per-agent cgroup v2 sub-directory management.

When CCB_PER_AGENT_SUBCGROUP=1 and the keeper scope has cgroup v2
delegation (`systemd-run -p Delegate=pids memory cpu`), the keeper can
create a sub-cgroup per provider agent and migrate the agent's process
PID into it. This gives each agent its own `pids.max`/`memory.max`
budget so one agent's heavy load cannot starve siblings.

Setup sequence (called in order at keeper startup):
    1. `setup_keeper_subcgroup()` - moves keeper PID into `<scope>/keeper/`
       so the scope root is empty of processes, then writes
       `+pids +memory` into `<scope>/cgroup.subtree_control` to enable
       child controllers. Required by cgroup v2's "no internal processes"
       rule.
    2. Per agent spawn: `move_pid_to_agent_subcgroup()` creates
       `<scope>/agent-<name>/` and migrates the agent's pane shell PID
       there. Child inherits pids.max/memory.max.

No-op when:
- Feature flag unset
- /sys/fs/cgroup/... not reachable
- Delegation not available (missing controllers, no write permission)

Never raises: all OSError paths degrade to a logged warning.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

SUBCGROUP_ENV = "CCB_PER_AGENT_SUBCGROUP"

DEFAULT_BUDGETS: dict[str, dict[str, int | str]] = {
    "codex":    {"pids_max": 400, "memory_max": "2G"},
    "claude":   {"pids_max": 300, "memory_max": "2G"},
    "gemini":   {"pids_max": 150, "memory_max": "1G"},
    "opencode": {"pids_max": 250, "memory_max": "1500M"},
    "droid":    {"pids_max": 250, "memory_max": "1500M"},
}

MoveResult = Literal["moved", "skipped_disabled", "skipped_unsupported", "failed"]
SetupResult = Literal[
    "setup", "already_setup", "skipped_disabled", "skipped_unsupported", "failed"
]
KEEPER_SUBCGROUP_NAME = "keeper"


def is_enabled() -> bool:
    return os.environ.get(SUBCGROUP_ENV, "").strip().lower() in (
        "1", "true", "yes", "on"
    )


def supports_cgroup_v2_delegation() -> bool:
    """Check that the process's cgroup has v2 delegation for pids+memory."""
    keeper_cg = _resolve_keeper_cgroup()
    if keeper_cg is None:
        return False
    controllers_file = keeper_cg / "cgroup.controllers"
    if not controllers_file.is_file():
        return False
    try:
        controllers = controllers_file.read_text().split()
    except OSError:
        return False
    if "pids" not in controllers or "memory" not in controllers:
        logger.warning(
            "cgroup v2 found but missing pids/memory controllers: %r (delegation incomplete)",
            controllers,
        )
        return False
    subtree_ctrl = keeper_cg / "cgroup.subtree_control"
    try:
        return subtree_ctrl.is_file() and os.access(str(subtree_ctrl), os.W_OK)
    except OSError:
        return False


def setup_keeper_subcgroup() -> SetupResult:
    """Relocate the current process (keeper) into a `keeper/` sub-cgroup.

    cgroup v2 enforces "no internal processes": a cgroup containing
    processes directly cannot enable controllers on `cgroup.subtree_control`.
    To give per-agent sub-cgroups real `pids.max` / `memory.max` budgets,
    the keeper's scope root must be empty of processes.

    This function:
    1. Creates `<scope>/keeper/`
    2. Writes the current PID into `keeper/cgroup.procs`
    3. Writes `+pids +memory` into `<scope>/cgroup.subtree_control`

    Must be called ONCE at keeper startup, before spawning any agents.
    Idempotent and best-effort; never raises.
    """
    if not is_enabled():
        return "skipped_disabled"
    keeper_cg = _resolve_keeper_cgroup()
    if keeper_cg is None:
        return "skipped_unsupported"

    controllers_file = keeper_cg / "cgroup.controllers"
    if not controllers_file.is_file():
        return "skipped_unsupported"
    try:
        controllers = controllers_file.read_text().split()
    except OSError:
        return "skipped_unsupported"
    if "pids" not in controllers or "memory" not in controllers:
        logger.warning(
            "setup_keeper_subcgroup: missing controllers %r", controllers
        )
        return "skipped_unsupported"

    keeper_sub = keeper_cg / KEEPER_SUBCGROUP_NAME
    try:
        keeper_sub.mkdir(exist_ok=True)
    except OSError as e:
        logger.warning("setup_keeper_subcgroup: mkdir failed: %s", e)
        return "failed"

    try:
        (keeper_sub / "cgroup.procs").write_text(f"{os.getpid()}\n")
    except OSError as e:
        logger.warning("setup_keeper_subcgroup: write procs failed: %s", e)
        return "failed"

    try:
        (keeper_cg / "cgroup.subtree_control").write_text("+pids +memory\n")
    except OSError as e:
        # Could be already enabled (EBUSY or EEXIST), which is fine
        logger.info(
            "setup_keeper_subcgroup: subtree_control already configured or unwritable: %s", e
        )

    logger.info("setup_keeper_subcgroup: moved keeper pid %d into %s",
                os.getpid(), keeper_sub)
    return "setup"


def move_pid_to_agent_subcgroup(
    pid: int,
    agent_name: str,
    provider: str,
    *,
    pids_max: int | None = None,
    memory_max: str | None = None,
) -> MoveResult:
    """Migrate `pid` into keeper-scope/agent-<agent_name>/. Best-effort."""
    if not is_enabled():
        return "skipped_disabled"
    if not supports_cgroup_v2_delegation():
        return "skipped_unsupported"

    keeper_cg = _resolve_keeper_cgroup()
    if keeper_cg is None:  # pragma: no cover (supports_... already checks this)
        return "skipped_unsupported"

    sub = keeper_cg / _sanitize_agent_name(agent_name)

    try:
        sub.mkdir(exist_ok=True)
    except OSError as e:
        logger.warning("agent subcgroup mkdir failed for %s: %s", sub, e)
        return "failed"

    # Enable delegation of pids+memory to children. Safe to retry each time.
    try:
        (keeper_cg / "cgroup.subtree_control").write_text("+pids +memory\n")
    except OSError as e:
        logger.warning("subtree_control write failed (non-fatal): %s", e)

    budget = DEFAULT_BUDGETS.get(provider, {})
    p_max = pids_max if pids_max is not None else int(budget.get("pids_max", 500))
    m_max = memory_max if memory_max is not None else str(budget.get("memory_max", "1G"))

    try:
        (sub / "pids.max").write_text(f"{p_max}\n")
    except OSError as e:
        logger.warning("pids.max write failed for %s: %s", sub, e)

    try:
        (sub / "memory.max").write_text(_parse_memory_size(m_max))
    except OSError as e:
        logger.warning("memory.max write failed for %s: %s", sub, e)

    try:
        (sub / "cgroup.procs").write_text(f"{pid}\n")
    except OSError as e:
        logger.warning("failed to move pid %d into %s: %s", pid, sub, e)
        return "failed"

    logger.info(
        "moved pid %d to %s (provider=%s pids_max=%d memory_max=%s)",
        pid, sub, provider, p_max, m_max,
    )
    return "moved"


def extract_agent_name_from_runtime_dir(runtime_dir: Path) -> str | None:
    """Given .../.ccb/agents/<agent_name>/.../..., return <agent_name>.

    Returns None if the path does not match the expected layout.
    """
    try:
        resolved = Path(runtime_dir).resolve()
    except OSError:
        return None
    for parent in [resolved, *resolved.parents]:
        gp = parent.parent
        if gp.name == "agents" and gp.parent.name == ".ccb":
            return parent.name
    return None


def _sanitize_agent_name(agent_name: str) -> str:
    """Normalize agent_name for use as a cgroup directory name."""
    safe = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in agent_name)
    return f"agent-{safe or 'unknown'}"


def _resolve_keeper_cgroup() -> Path | None:
    """Return the Path to the current process's cgroup under /sys/fs/cgroup/."""
    try:
        content = Path("/proc/self/cgroup").read_text()
    except OSError:
        return None
    for line in content.splitlines():
        parts = line.split(":", 2)
        # cgroup v2 unified hierarchy entry: "0::/path"
        if len(parts) == 3 and parts[0] == "0":
            relative = parts[2].lstrip("/")
            return Path("/sys/fs/cgroup") / relative
    return None


def _parse_memory_size(value: str) -> str:
    """Convert a size like '2G' / '512M' / '1024K' / '1073741824' to bytes string."""
    v = value.strip().upper()
    if not v:
        raise ValueError("memory size is empty")
    multiplier = 1
    if v.endswith("G"):
        multiplier = 1024 ** 3
        v = v[:-1]
    elif v.endswith("M"):
        multiplier = 1024 ** 2
        v = v[:-1]
    elif v.endswith("K"):
        multiplier = 1024
        v = v[:-1]
    return f"{int(v) * multiplier}\n"
