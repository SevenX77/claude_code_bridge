from __future__ import annotations

from pathlib import Path
import shlex

from provider_profiles import ResolvedProviderProfile, provider_api_env_keys


def build_gemini_env_prefix(
    *,
    profile: ResolvedProviderProfile | None = None,
    extra_env: dict[str, str] | None = None,
    gemini_home: Path | None = None,
) -> str:
    api_keys = provider_api_env_keys("gemini")
    explicit_env = explicit_api_env(profile=profile, extra_env=extra_env, api_keys=api_keys)
    parts = cleared_api_env_parts(profile=profile, api_keys=api_keys, gemini_home=gemini_home)
    exports = export_clause(explicit_env)
    if exports:
        parts.append(exports)
    return "; ".join(parts)


def explicit_api_env(
    *,
    profile: ResolvedProviderProfile | None,
    extra_env: dict[str, str] | None,
    api_keys: set[str],
) -> dict[str, str]:
    explicit_env: dict[str, str] = {}
    if profile is not None:
        explicit_env.update(selected_api_env(profile.env, api_keys=api_keys))
    if extra_env:
        explicit_env.update(selected_api_env(extra_env, api_keys=api_keys))
    return explicit_env


def selected_api_env(values: dict[str, str], *, api_keys: set[str]) -> dict[str, str]:
    return {key: value for key, value in values.items() if key in api_keys}


def cleared_api_env_parts(
    *,
    profile: ResolvedProviderProfile | None,
    api_keys: set[str],
    gemini_home: Path | None = None,
) -> list[str]:
    # Force-clear API envs when OAuth creds present in the resolved gemini home.
    # Reason: when the user has logged in via Google OAuth (oauth_creds.json
    # exists), inheriting a stale GEMINI_API_KEY from the master shell env
    # makes the agent prefer the dead API key over the working OAuth flow.
    # Symptom: agent CLI shows API_KEY_INVALID even though `gemini` works
    # interactively for the user.
    if gemini_home is not None:
        try:
            if (Path(gemini_home) / "oauth_creds.json").exists():
                return [f"unset {key}" for key in sorted(api_keys)]
        except Exception:
            pass
    if profile is None or profile.inherit_api:
        return []
    return [f"unset {key}" for key in sorted(api_keys)]


def export_clause(explicit_env: dict[str, str]) -> str:
    rendered = " ".join(
        f"{key}={shlex.quote(value)}"
        for key, value in sorted(explicit_env.items())
        if str(value).strip()
    )
    return f"export {rendered}" if rendered else ""


__all__ = ["build_gemini_env_prefix"]
