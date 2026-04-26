from __future__ import annotations

from pathlib import Path

from agents.models import AgentValidationError, normalize_agent_name, parse_layout_spec

from ..common import ConfigLoadResult, ConfigValidationError
from ..parsing import validate_project_config
from ..paths import project_config_path

try:  # pragma: no branch
    import tomllib as _toml_reader
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    try:
        import tomli as _toml_reader  # type: ignore[no-redef]
    except ModuleNotFoundError:  # pragma: no cover - external fallback
        import toml as _toml_reader  # type: ignore[no-redef]


def _build_compact_agent_record(provider: str, *, workspace_mode: str) -> dict[str, object]:
    return {
        'provider': provider,
        'target': '.',
        'workspace_mode': workspace_mode,
        'restore': 'auto',
        'permission': 'manual',
    }


def _strip_layout_comments(line: str) -> str:
    return line.split('#', 1)[0].split('//', 1)[0].strip()


def _split_compact_config_text(text: str) -> tuple[str, list[str]]:
    layout_lines: list[str] = []
    budget_lines: list[str] = []
    for line in text.splitlines():
        cleaned = _strip_layout_comments(line)
        if not cleaned:
            continue
        if cleaned.lower().startswith('agent-budget:'):
            budget_lines.append(cleaned.split(':', 1)[1].strip())
        else:
            layout_lines.append(cleaned)
    return '\n'.join(layout_lines).strip(), budget_lines


def _normalize_budget_agent_name(path: Path, raw_name: str) -> str:
    try:
        return normalize_agent_name(raw_name)
    except AgentValidationError as exc:
        raise ConfigValidationError(f'{path}: invalid agent-budget name {raw_name!r}: {exc}') from exc


def _parse_agent_budget_text(text: str, *, path: Path) -> dict[str, dict[str, object]]:
    budgets: dict[str, dict[str, object]] = {}
    for raw_entry in text.split(';'):
        entry = raw_entry.strip()
        if not entry:
            continue
        if '=' not in entry:
            raise ConfigValidationError(
                f"{path}: invalid agent-budget entry {entry!r}; expected name=pids:N,mem:SIZE"
            )
        raw_name, raw_limits = entry.split('=', 1)
        agent_name = _normalize_budget_agent_name(path, raw_name.strip())
        if agent_name in budgets:
            raise ConfigValidationError(f'{path}: duplicate agent-budget entry for {agent_name}')
        limits: dict[str, object] = {}
        for raw_limit in raw_limits.split(','):
            limit = raw_limit.strip()
            if not limit:
                continue
            if ':' not in limit:
                raise ConfigValidationError(
                    f"{path}: invalid agent-budget limit {limit!r}; expected pids:N or mem:SIZE"
                )
            key, raw_value = (part.strip() for part in limit.split(':', 1))
            if key == 'pids':
                if 'pids_max' in limits:
                    raise ConfigValidationError(f'{path}: duplicate pids budget for {agent_name}')
                try:
                    pids_max = int(raw_value)
                except ValueError as exc:
                    raise ConfigValidationError(
                        f'{path}: agent-budget pids for {agent_name} must be a positive integer'
                    ) from exc
                if pids_max <= 0:
                    raise ConfigValidationError(
                        f'{path}: agent-budget pids for {agent_name} must be a positive integer'
                    )
                limits['pids_max'] = pids_max
            elif key == 'mem':
                if 'memory_max' in limits:
                    raise ConfigValidationError(f'{path}: duplicate mem budget for {agent_name}')
                if not raw_value:
                    raise ConfigValidationError(f'{path}: agent-budget mem for {agent_name} cannot be empty')
                limits['memory_max'] = raw_value
            else:
                raise ConfigValidationError(
                    f"{path}: unknown agent-budget limit {key!r}; expected pids or mem"
                )
        if not limits:
            raise ConfigValidationError(f'{path}: agent-budget entry for {agent_name} is empty')
        budgets[agent_name] = limits
    return budgets


def _apply_agent_budgets(
    agents: dict[str, dict[str, object]],
    budget_texts: list[str],
    *,
    path: Path,
) -> None:
    for budget_text in budget_texts:
        for agent_name, budget in _parse_agent_budget_text(budget_text, path=path).items():
            if agent_name not in agents:
                raise ConfigValidationError(f'{path}: agent-budget references unknown agent {agent_name}')
            for key, value in budget.items():
                if key in agents[agent_name]:
                    raise ConfigValidationError(f'{path}: duplicate {key} budget for {agent_name}')
                agents[agent_name][key] = value


def _raise_invalid_compact_token(path: Path, token: str) -> None:
    raise ConfigValidationError(
        f"{path}: invalid token {token!r}; expected 'agent_name:provider' or 'cmd'"
    )


def _consume_compact_leaf(
    leaf,
    *,
    path: Path,
    default_agents: list[str],
    agents: dict[str, dict[str, object]],
    cmd_enabled: bool,
) -> bool:
    token = leaf.name.strip()
    normalized_name = token.lower()
    if normalized_name == 'cmd':
        if leaf.provider is not None:
            raise ConfigValidationError(f"{path}: reserved token 'cmd' cannot declare a provider")
        if cmd_enabled:
            raise ConfigValidationError(f'{path}: compact config cannot define cmd more than once')
        return True
    if leaf.provider is None:
        _raise_invalid_compact_token(path, token)
    if normalized_name in agents:
        raise ConfigValidationError(f'{path}: duplicate agent name in compact config: {token}')
    default_agents.append(token)
    agents[normalized_name] = _build_compact_agent_record(
        leaf.provider,
        workspace_mode='git-worktree' if str(leaf.workspace_mode or '').strip() == 'worktree' else 'inplace',
    )
    return cmd_enabled


def _parse_compact_config_document(text: str, *, path: Path) -> dict[str, object]:
    layout_text, budget_lines = _split_compact_config_text(text)
    if not layout_text:
        raise ConfigValidationError(f'{path}: config is empty')
    try:
        layout = parse_layout_spec(layout_text)
    except Exception as exc:
        raise ConfigValidationError(f'{path}: invalid compact layout: {exc}') from exc

    default_agents: list[str] = []
    agents: dict[str, dict[str, object]] = {}
    cmd_enabled = False
    for leaf in layout.iter_leaves():
        cmd_enabled = _consume_compact_leaf(
            leaf,
            path=path,
            default_agents=default_agents,
            agents=agents,
            cmd_enabled=cmd_enabled,
        )
    if not default_agents:
        raise ConfigValidationError(f'{path}: compact config must define at least one agent')
    _apply_agent_budgets(agents, budget_lines, path=path)

    return {
        'version': 2,
        'default_agents': default_agents,
        'agents': agents,
        'cmd_enabled': cmd_enabled,
        'layout': layout.render(),
    }


def _looks_like_rich_config(text: str) -> bool:
    for line in text.splitlines():
        body = line.split('#', 1)[0].strip()
        if not body:
            continue
        if body.lower().startswith('agent-budget:'):
            continue
        if body.startswith('[') or '=' in body:
            return True
    return False


def _parse_toml_config_document(text: str, *, path: Path) -> dict[str, object]:
    try:
        if hasattr(_toml_reader, 'loads'):
            document = _toml_reader.loads(text)
        else:  # pragma: no cover
            document = _toml_reader.load(text)
    except Exception as exc:
        raise ConfigValidationError(f'{path}: invalid TOML config: {exc}') from exc
    if not isinstance(document, dict):
        raise ConfigValidationError(f'{path}: TOML config must decode to a table/object')
    result = dict(document)
    budget_value = result.pop('agent-budget', None)
    if budget_value is not None:
        if not isinstance(budget_value, str):
            raise ConfigValidationError(f'{path}: agent-budget must be a string')
        raw_agents = result.get('agents')
        if not isinstance(raw_agents, dict):
            raise ConfigValidationError(f'{path}: agent-budget requires an agents table')
        agents = {
            _normalize_budget_agent_name(path, str(name)): dict(value)
            for name, value in raw_agents.items()
            if isinstance(value, dict)
        }
        if len(agents) != len(raw_agents):
            raise ConfigValidationError(f'{path}: agents table values must be tables/objects')
        _apply_agent_budgets(agents, [budget_value], path=path)
        result['agents'] = agents
    return result


def _load_config_document(path: Path) -> dict[str, object]:
    text = path.read_text(encoding='utf-8')
    if _looks_like_rich_config(text):
        return _parse_toml_config_document(text, path=path)
    return _parse_compact_config_document(text, path=path)


def load_project_config(project_root: Path) -> ConfigLoadResult:
    project_path = project_config_path(project_root)
    if project_path.exists():
        return ConfigLoadResult(
            config=validate_project_config(_load_config_document(project_path), source_path=project_path),
            source_path=project_path,
            used_default=False,
        )
    raise ConfigValidationError(f'config not found for project {project_root}')


__all__ = ['load_project_config']
