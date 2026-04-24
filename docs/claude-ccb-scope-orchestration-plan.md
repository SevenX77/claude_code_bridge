# Claude + CCB Scope Orchestration Plan

**Status**: Design finalized 2026-04-24. Phase 0 + Phase 1 MVP + Phase 2 + Phase 3 P3-2 per-agent sub-cgroup **implemented** (see `~/.claude/TASK-PLAN-2026-04-24-handoff.md` for current execution state). Per-section `(DONE)` / timing labels below this header may lag — treat the handoff as canonical for current progress.
**Last updated**: 2026-04-24
**Owner**: sevenx
**Context window**: this doc is written to survive Claude context clears — a new Claude reading this should be able to continue the work without prior conversation state.

---

## 1. Problem Statement

The user's existing setup wraps every `claude` invocation in a `systemd-run --user --scope -p TasksMax=150` wrapper (`/usr/local/bin/claude-sandbox`), which was intended as resource isolation + cleanup-on-exit.

**Problem discovered**: when Claude invokes `ccb` (via Bash tool), the spawned CCB daemon + keeper + N provider CLI agents (codex/claude/gemini) **inherit Claude's cgroup scope**, consuming Claude's `TasksMax=150` budget together with Claude itself. Symptoms:

- `Cannot fork` errors during routine Bash calls (scope task count approaches/exceeds 150)
- Codex CLI panic-crashing with Rust `WouldBlock: Resource temporarily unavailable` when trying to spawn worker threads (actual case: `log_line: Pane is dead (status 0)` followed by backtrace from `rustlib/src/rust/library/std/src/thread/functions.rs:131`)
- CCB hit this ~128/150 tasks when running with 5 agents, leaving zero safety margin for any ad-hoc work

Symmetric problem also found in `agent-harness` project: DeerFlow subagent executor creates module-level `ThreadPoolExecutor` with 6 non-daemon worker threads × 2 pools. When integration tests trigger `import`, threads leak and block interpreter shutdown (observed 4.52s hang). `shutdown(wait=False, cancel_futures=True)` does not fix this because non-daemon running workers cannot be forcibly killed.

**Archetype**: library-created background resources (OS processes, threads, tmux daemons, socket listeners) that outlive test/task scope because teardown paths are insufficient.

---

## 2. Root Cause (Confirmed by Verification)

- `cat /proc/$$/cgroup` from a Claude Bash invocation shows `.../claude-XXX.scope`
- `cat /proc/<ccb-keeper-pid>/cgroup` shows the **same** claude scope → CCB inherits Claude's cgroup
- `systemctl --user show session-11.scope -p TasksMax --value` returns `9377` (systemd default `DefaultTasksMax`, = 15% of `kernel.pid_max=4194304`)
- `TasksMax=150` is **self-imposed** by `/usr/local/bin/claude-sandbox`; it is **1/62 of system default**
- `systemctl --user stop <scope>` kills the entire scope and all descendants (SIGTERM → 90s wait → SIGKILL). This is cgroup v2's mandatory cleanup mechanism.
- Systemd scopes created via `systemd-run --user --scope` are placed as **sibling** of caller's scope (under `app.slice`), NOT nested. → Siblings have **independent** TasksMax budgets.

CCB upstream (per `docs/ccbd-lifecycle-stability-plan.md` §2.1) has identified the "orphan process leak" symptom but has never addressed the "CCB shares caller's cgroup budget" root cause. The CHANGELOG's "Retry transient tmux respawn fork failures" is a band-aid (retry after fork fails) — it does not solve the scope being saturated.

---

## 3. Final Design Decisions (sevenx, 2026-04-24)

User approved these 7 points verbatim:

1. **Orphan protection: A + B double defense**
   - (A) Watchdog process inside each created scope monitors the orchestrator PID. Orchestrator dies → watchdog triggers `systemctl --user stop` on its own scope
   - (B) Orchestrator registers an `atexit` hook that enumerates and stops all scopes it created

2. **Context refresh semantics**
   - Active restart (task boundary, manual refresh): launch CCB agents with `--new-session` (codex) / equivalent `--new` flags → fresh conversation state
   - Crash restart (unexpected scope death, auto-recovery): launch with `--continue` / `--resume` → inherit prior context
   - Orchestrator distinguishes by checking exit reason

3. **Per-agent independent sub-scope**: direction correct, **deferred to Phase 3** (requires CCB upstream change — per-agent scope nesting inside CCB scope, each `codex`/`claude`/`gemini` CLI gets its own sub-scope with per-provider `TasksMax`)
   - sevenx note: codex/claude need bigger per-agent budgets because they may run tests; gemini does conversation/research/read-only, can be smaller
   - Each agent's "clean baseline" process count differs per provider → per-agent sizing should be configurable

4. **Task boundary**: master Claude (orchestrator) decides autonomously. Given clear tools and the rule "task ends → clean up all scopes I created besides my own", Claude determines when to start/stop scopes based on task transitions.

5. **Infrastructure**: add orchestrator tool + persistent tracking + janitor. See Phase 1 below.

6. **CCB lifecycle policy**: **per-task** (no longer a daemon that persists across tasks). Each task topic → Claude launches fresh CCB scope → task done → Claude destroys scope. Agents refresh naturally per task; no "long-running CCB accumulating weird state".

7. **Test isolation thresholds**:
   - Unit tests / small integration → run inside CCB agent (agent's cgroup)
   - Large integration / e2e → **each e2e gets its own dedicated scope**, destroyed on completion
   - **Threshold**: estimated duration > 30s OR estimated concurrent threads > 20 → dedicated scope
   - **Master Claude must NOT batch-spawn test processes directly** from its own scope — tests run inside CCB agents or dedicated e2e scopes

---

## 4. Five Identified Vulnerabilities (and Mitigations)

| # | Vulnerability | Severity | Mitigation |
|---|--------------|----------|------------|
| V1 | **Orphan sibling scope**: Claude dies → scopes it created are NOT auto-reaped (sibling relationship has no systemd parent-child lifecycle binding by default) | High | A + B double defense (see Decision #1). Watchdog inside scope polls orchestrator PID every 5s; atexit hook lists and stops created scopes. |
| V2 | **Context refresh is two-layered**: killing scope only clears runtime (processes + tmux panes). Provider CLI session files (`~/.codex/sessions/*`, `.ccb/.codex-agentN-session`) persist → restarted agent `--continue` resumes old conversation, giving false "refreshed" appearance. | Medium | Decision #2: active restart explicitly deletes session files AND uses `--new-session`; crash restart uses `--continue`. Orchestrator mediates. |
| V3 | **All agents share CCB scope**: one agent's heavy pytest saturates CCB scope → other agents suffocate. | Medium-High | Short-term: keep total CCB scope budget large (current `TasksMax=1000`). Long-term: Phase 3 per-agent sub-scope (Decision #3). e2e goes to dedicated scope anyway (Decision #7). |
| V4 | **"Task boundary" is fuzzy**: too fine-grained → startup overhead dominates; too coarse → defeats refresh purpose. | Low-Medium | Heuristic: (a) explicit user signal ("new task"), (b) Claude detects semantic topic shift, (c) elapsed-time watchdog (force refresh every 2h), (d) scope utilization (>80% → force refresh). Implementation: Phase 2. |
| V5 | **Master Claude lacks required infrastructure**: no scope tracking, no atexit cleanup, no orphan sweep, no task-boundary detection. | Medium | Phase 1 builds `claude-ccb-orchestrator` shell+python tool providing these primitives. |

---

## 5. Phased Implementation

### Phase 0 — Temporary unblock (DONE)

- [x] Set `TasksMax=1000` live on all running claude scopes via `systemctl --user set-property` (no restart required)
- [x] Clean all garbage: killed stuck `npm install -g @google/gemini-cli` (2 instances, 11h / 8h stuck), killed orphan codex bridge from pytest-52 (11h orphan), removed `/tmp/pytest-of-sevenx/*`
- [x] Restarted `claude_code_bridge` CCB cleanly, verified all agents ready
- [x] Minimized `.ccb/ccb.config` to `cmd, agent3:claude` only (1 agent baseline). Original backed up as `.ccb/ccb.config.bak-20260424-095335`. Add more agents by editing this file + `ccb kill && ccb`.
- [ ] **User pending**: `sudo sed -i.bak 's/TasksMax=150/TasksMax=1000/' /usr/local/bin/claude-sandbox` to make the change permanent for future `claude` invocations. (Live change does not survive Claude scope restart.)

### Phase 1 — MVP Orchestrator (2-3 days)

Deliverables:
- `~/.local/bin/claude-ccb-orchestrator` (shell + python mix)
  - `start-task-scope <task-name> [--agents agent1,agent2] [--tasks-max N]` → creates sibling scope, launches CCB inside it, prints scope name + PID to stdout
  - `stop-task-scope <task-name>` → `systemctl --user stop <scope>`, verifies clean kill
  - `list-my-scopes` → reads tracking file, returns JSON
  - `cleanup-orphans` → scans `claude-ccb-*.scope` against tracking file, stops untracked
- Tracking persistence: `~/.cache/claude-ccb-scopes/<master-claude-pid>.json`
- Claude prompts / CLAUDE.md addendum: "before starting heavy work, call orchestrator to create a scope; before responding 'done', call orchestrator to clean up"

### Phase 2 — Robustness (1-2 weeks)

- atexit hook registration in `claude-sandbox` wrapper (before `exec systemd-run`, set `trap` on EXIT that runs `claude-ccb-orchestrator cleanup-my-scopes $$`)
- systemd user timer `claude-ccb-janitor.timer` (hourly, runs `cleanup-orphans`)
- Task-boundary heuristic (V4 mitigation implementation)
- Watchdog-in-scope improvements (V1 A): embed a tiny shell loop as the last process in each scope that polls orchestrator PID

### Phase 3 — Upstream CCB changes (weeks to months)

- Per-agent sub-scope inside CCB keeper (V3 long-term fix)
- `ccbd` as proper systemd user service template (`ccbd@<project>.service`), so `ccb` CLI triggers `systemctl --user start ccbd@XXX.service` — ccbd lives in its own service scope, not caller's
- Per-agent start/stop CLI (`ccb start agent5`, `ccb stop agent5`) so user can dynamically add/remove agents without editing `ccb.config` + full restart
- Upstream strategy: **implement in user's fork first**. Open issue on upstream for discussion only if user decides to share. Don't block on maintainer acceptance.

---

## 6. Immediate Pending Tasks (priority-ordered, actionable)

Written so the next Claude can pick up without re-deriving context.

### P0 — Must do before strategic work resumes

- [ ] **P0-1** `test/conftest.py` tmux leak fix. Plan already drafted and approved by user (2026-04-24). See Section 7 for plan content. Implementation: append two autouse fixtures (`_cleanup_ccb_tmux_per_test`, `_cleanup_ccb_tmux_session_end`) to `test/conftest.py`. Next Claude can proceed directly — user has explicitly approved this plan.
- [ ] **P0-2** `sudo sed -i.bak 's/TasksMax=150/TasksMax=1000/' /usr/local/bin/claude-sandbox`. User-only action (needs sudo). One-shot. Must remind user if they forget.

### P1 — Phase 1 MVP

- [ ] **P1-1** Draft `claude-ccb-orchestrator` tool spec (interface + file formats + example flows). Delegate implementation to codex (inside a fresh CCB agent) once spec is approved.
- [ ] **P1-2** Implement `start-task-scope`. First iteration: hardcoded `TasksMax=500`, no watchdog, basic tracking only.
- [ ] **P1-3** Implement `stop-task-scope` + `list-my-scopes` + `cleanup-orphans`.
- [ ] **P1-4** Add watchdog-in-scope (V1 mitigation A).
- [ ] **P1-5** Add atexit hook in `claude-sandbox` (V1 mitigation B).

### P2 — Phase 2

- [ ] **P2-1** Task-boundary auto-detection heuristic.
- [ ] **P2-2** Janitor timer.
- [ ] **P2-3** Context refresh logic (V2 mitigation: active=new-session+delete-session-file; crash=continue).

### P3 — Phase 3 (upstream)

- [ ] **P3-1** Design per-agent sub-scope inside CCB keeper. Write as RFC-style design doc.
- [ ] **P3-2** Implement in fork.
- [ ] **P3-3** Optionally propose to CCB upstream via issue (user's call).

### Cross-cutting

- [ ] **X-1** Send message to agent-harness Claude (scope 3) about `shutdown(wait=True)` fix and TasksMax=1000. Message draft already written — user will copy+paste in their iPad Claude Code chat.
- [ ] **X-2** Verify DeerFlow subagent executor thread leak mitigation (agent-harness side). Expected target: 4.52s hang → < 0.5s.

---

## 7. Detailed P0-1 Plan (conftest.py tmux leak fix)

Context for next Claude: this plan was approved by user on 2026-04-24. Reviewer path (Codex) failed due to TasksMax=150 saturation (now resolved with TasksMax=1000). User authorized direct implementation without further peer review.

**Target file**: `test/conftest.py` (append only, do not modify existing fixtures)

**Root cause**: `test/test_v2_phase2_entrypoint.py` contains ~20+ tests using `_run_ccb([...])` (line 29-44) and `_run_phase2_local(...)` (line 112) which `subprocess.run` the `ccb` CLI. The `ccb` subprocess starts a CCB keeper that double-forks into an independent tmux daemon (socket at `tmp_path/.ccb/ccbd/tmux.sock` or `/run/user/$UID/ccb-runtime/tmux-*.sock`). The test's in-process `app.shutdown()` cannot reach those daemons. Result: each test run leaks 1+ tmux daemons per `ccb` subprocess invocation.

Evidence: `/tmp/pytest-of-sevenx/pytest-52,57,58/` accumulated 25+ orphaned `tmux: server` processes (ETIME 2h+) before we cleaned them.

**Solution code** (append to `test/conftest.py`):

```python
# ============================================================
# CCB tmux daemon leak cleanup.
# Root cause: tests in test_v2_phase2_entrypoint.py spawn `ccb` CLI
# via subprocess. Those subprocesses start the CCB keeper which wraps
# itself in an independent tmux daemon (socket under
# tmp_path/.ccb/ccbd/tmux.sock, or /run/user/$UID/ccb-runtime/tmux-*.sock).
# The test's in-process app.shutdown() cannot reach those daemons.
# ============================================================

import glob
import logging
import subprocess

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
        return  # tmux absent (e.g., Windows CI) — nothing to kill
    except subprocess.TimeoutExpired:
        _leak_logger.warning("cleanup: tmux kill-server %s timed out after 5s", sock)


def _runtime_socket_pattern() -> str:
    return f'/run/user/{os.getuid()}/ccb-runtime/tmux-*.sock'


@pytest.fixture(autouse=True)
def _cleanup_ccb_tmux_per_test(tmp_path):
    """After each test, kill CCB tmux daemons spawned by subprocess ccb calls.

    Scope:
    - tmux sockets found under this test's tmp_path (safe: tmp_path is
      exclusive to this test).
    - sockets newly added to /run/user/$UID/ccb-runtime/ during this test
      (diff of snapshots, to avoid killing user's legitimate CCB runtime
      sockets that exist in the same dir).
    """
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
    """Session-end sweep as belt-and-suspenders.

    If any per-test cleanup missed (e.g., fixture exception), catch
    remaining daemons here by scanning the whole pytest base tmp and
    diffing the runtime dir from session-start.
    """
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
```

**Key design decisions** (justified):
1. conftest-level fixture (not per-test try/finally): one change covers all 254 tests; no-op cost for the ~234 tests that don't spawn ccb subprocesses.
2. No pre-session cleanup of `/tmp/pytest-of-*/pytest-*/**`: would risk killing concurrent pytest runs. Historical leftovers cleaned manually.
3. Snapshot-diff on `/run/user/$UID/ccb-runtime/`: avoids clobbering user's legitimate CCB runtime sockets. Only kill what was added DURING this test/session.
4. `tmp_path.rglob` safe: pytest's `tmp_path` is exclusive per-test.
5. `except FileNotFoundError: return`: tmux absent is not an error.
6. `except subprocess.TimeoutExpired: logger.warning`: real anomaly, must be observable.
7. No catch-all `except Exception`: compliant with user's zero-silent-failure rule.

**What is deliberately NOT changed**:
- No changes to `test_v2_phase2_entrypoint.py` (20+ tests stay as-is, too error-prone to touch each).
- No change to `lib/ccbd/*.py` production code (daemon lifecycle root-fix is Phase 3).

**Implementation step** (next Claude can do this directly):
Use Edit tool to append the code block above to end of `test/conftest.py`. Verify with `python -c "import ast; ast.parse(open('test/conftest.py').read())"`. Run a sample test from `test_v2_phase2_entrypoint.py` and confirm no tmux leak in `/tmp/pytest-of-sevenx` afterward.

---

## 8. Open Design Decisions (still need user call)

Postponed until corresponding phase begins. Listed here so next Claude knows what NOT to decide unilaterally.

- **D1** CCB "常驻 vs 按任务"的 user-facing trigger: manual command `/new-ccb-session` vs auto-detection? (Decision #6 picked per-task but trigger mechanism not finalized.)
- **D2** Per-agent TasksMax defaults: codex/claude vs gemini numbers not fixed. Need empirical baseline measurement.
- **D3** Exact session file cleanup list for "active refresh" (Decision #2): which files to delete? `~/.codex/sessions/<id>.json`? `.ccb/.codex-agentN-session`? Both? Need codex/claude session file format understanding.

---

## 9. Upstream Contribution Strategy (sevenx's position)

- User owns the fork. All changes land in user's fork first.
- User does NOT wait for maintainer acceptance. If upstream picks it up, bonus; if not, open an issue for discussion only.
- All GitHub operations (fork / branch / PR / issue) done by master Claude on user's behalf. User reviews drafts before anything is published.
- RFC issue for Phase 3 is OPTIONAL, not a prerequisite.

---

## 10. Verification Appendix

Commands + outputs useful for the next Claude to verify current state.

```bash
# 1. Verify I'm in a Claude scope
cat /proc/$$/cgroup
# Expected: 0::/user.slice/user-1001.slice/user@1001.service/app.slice/claude-<id>-<pid>.scope

# 2. Check current TasksMax on running claude scopes (should be 1000 as of 2026-04-24)
for s in $(systemctl --user list-units --type=scope --plain --no-legend | awk '/claude-/{print $1}'); do
  echo "$s -> TasksMax=$(systemctl --user show "$s" -p TasksMax --value) TasksCurrent=$(systemctl --user show "$s" -p TasksCurrent --value)"
done

# 3. Verify ccb.config is minimized (after 2026-04-24 cleanup)
cat /home/sevenx/coding/claude_code_bridge/.ccb/ccb.config
# Expected: cmd, agent3:claude

# 4. Check for garbage processes (should be clean)
ps -eo pid,ppid,etime,args --no-headers | awk '$2==1' | grep -iE 'npm install|pytest-of|bridge' | grep -v grep

# 5. CCB state (healthy)
ccb ping ccbd
# Expected: mount_state: mounted, health: healthy

# 6. Check tmp leftovers
find /tmp/pytest-of-sevenx -name 'tmux.sock' 2>/dev/null
ls /run/user/1001/ccb-runtime/ | grep -c tmux-  # should be 0 or only legitimate
```

**Key verified facts**:
- `systemd-run --user --scope` creates sibling scopes under `app.slice`
- `systemctl --user set-property <scope> TasksMax=N` works live, no restart
- `systemctl --user stop <scope>` cleanly reaps all descendants (SIGTERM → SIGKILL)
- CCB's `keeper_main.py`, `main.py`, and tmux server all share the caller's scope (verified via `/proc/<pid>/cgroup`)
- Session-scope TasksMax default = 9377 (60× larger than claude-sandbox's 150)
- `kernel.pid_max` = 4194304 (system ceiling, irrelevant to per-scope limits)

---

## 11. Related Files

- `/usr/local/bin/claude-sandbox` — user's Claude launcher wrapper (sets TasksMax, MemoryMax)
- `/home/sevenx/coding/claude_code_bridge/.ccb/ccb.config` — CCB agent configuration (minimized 2026-04-24)
- `/home/sevenx/coding/claude_code_bridge/.ccb/ccb.config.bak-20260424-095335` — pre-minimize backup (5 agents)
- `/home/sevenx/coding/claude_code_bridge/test/conftest.py` — target of P0-1 fix
- `/home/sevenx/coding/claude_code_bridge/test/test_v2_phase2_entrypoint.py` — file with leaking tests (5089 lines)
- `/home/sevenx/coding/claude_code_bridge/docs/ccbd-lifecycle-stability-plan.md` — CCB's own analysis (doesn't cover cgroup sharing issue)
- `/home/sevenx/.claude/projects/-home-sevenx/memory/MEMORY.md` — user's persistent memory index (entry added for this plan)

---

## End of Plan
