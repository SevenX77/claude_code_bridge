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

import functools
import logging
import os
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

SUBCGROUP_ENV = "CCB_PER_AGENT_SUBCGROUP"

# cgroup v2 unified hierarchy is mounted at /sys/fs/cgroup when the
# system runs "unified" mode. The presence of cgroup.controllers at
# that root is the canonical sign: hybrid mode puts v2 under a child
# like /sys/fs/cgroup/unified and leaves the root as tmpfs without
# this file; v1-only systems never create it. Checking this file also
# probes read permission, which is what callers actually need.
_CGROUP_V2_ROOT = Path("/sys/fs/cgroup")
_CGROUP_V2_SENTINEL = _CGROUP_V2_ROOT / "cgroup.controllers"


@functools.lru_cache(maxsize=1)
def _is_cgroup_v2_available() -> bool:
    """Return True iff this host runs cgroup v2 unified hierarchy.

    Cached for the lifetime of the process: cgroup layout does not
    change at runtime in practice. Every public entrypoint in this
    module (is_enabled-gated setup / move calls) short-circuits on
    False so non-v2 hosts (v1 hybrid, container without cgroup,
    macOS dev) become a full no-op instead of relying on downstream
    /proc/self/cgroup parsing to fail silently.
    """
    try:
        return _CGROUP_V2_SENTINEL.is_file()
    except OSError:
        return False

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
    if not _is_cgroup_v2_available():
        return False
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
    2. Moves ALL scope-root PIDs into `keeper/cgroup.procs`
    3. Writes `+pids +memory` into `<scope>/cgroup.subtree_control`

    Idempotent and best-effort; never raises. If the caller is already
    inside `keeper/`, returns "already_setup" without creating a nested
    `keeper/keeper/`.
    """
    if not is_enabled():
        return "skipped_disabled"
    if not _is_cgroup_v2_available():
        return "skipped_unsupported"
    keeper_cg = _resolve_scope_root_cgroup()
    if keeper_cg is None:
        return "skipped_unsupported"

    # If our cgroup is already the keeper sub-dir, subsequent setup calls
    # are no-ops (the scope root above us is already configured).
    my_cg = _resolve_keeper_cgroup()
    if my_cg is not None and my_cg.name == KEEPER_SUBCGROUP_NAME:
        return "already_setup"

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

    # Move ALL scope-root PIDs (including this process and any sibling
    # daemons like ccb CLI, ccbd daemon_process) into keeper/. cgroup v2
    # "no internal processes" rule requires the scope root be empty of
    # processes BEFORE we can enable `+pids +memory` on subtree_control.
    _drain_scope_root_into_keeper(keeper_cg, keeper_sub)

    try:
        (keeper_cg / "cgroup.subtree_control").write_text("+pids +memory\n")
    except OSError as e:
        # Still failing means some child races with us - log and continue.
        # agent-<name> children will still work IF subtree_control is
        # already enabled from a previous run.
        logger.warning(
            "setup_keeper_subcgroup: subtree_control write failed: %s "
            "(agent limits may not enforce)", e,
        )

    logger.info("setup_keeper_subcgroup: moved scope-root procs into %s",
                keeper_sub)
    return "setup"


def _drain_scope_root_into_keeper(scope_cg: Path, keeper_sub: Path) -> None:
    """Move every PID currently in scope root into keeper/.

    Retry up to 3 passes because children can be forked between reads.
    """
    procs_file = scope_cg / "cgroup.procs"
    keeper_procs_file = keeper_sub / "cgroup.procs"
    for _ in range(3):
        try:
            root_pids = procs_file.read_text().split()
        except OSError:
            return
        if not root_pids:
            return
        for pid in root_pids:
            if not pid.isdigit():
                continue
            try:
                keeper_procs_file.write_text(f"{pid}\n")
            except OSError:
                # Process may have exited, or cgroup may have raced.
                # Non-fatal; next pass will catch remaining.
                continue


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

    # Agent cgroups are created as siblings of keeper/ under the scope root,
    # not inside the caller's current cgroup (which may itself be keeper/).
    keeper_cg = _resolve_scope_root_cgroup()
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


def _resolve_scope_root_cgroup() -> Path | None:
    """Return the scope-root cgroup (one level above keeper/ if we're inside it)."""
    current = _resolve_keeper_cgroup()
    if current is None:
        return None
    if current.name == KEEPER_SUBCGROUP_NAME:
        return current.parent
    return current


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
