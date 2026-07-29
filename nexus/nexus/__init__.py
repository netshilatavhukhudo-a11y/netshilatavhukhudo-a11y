"""NEXUS — a general-purpose autonomous agent.

An LLM that can call functions, in a loop, with memory and guardrails.

This module owns exactly one thing: loading and validating the agent config.
Every runtime limit and permission rule lives in `config/agent.json` so the
rest of the codebase contains no magic numbers.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

__all__ = ["PROJECT_ROOT", "ConfigError", "load_config", "workspace_dir"]

# The repo root — the directory containing `config/`, `run.py`, and this package.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

_DEFAULT_CONFIG_PATH = "config/agent.json"

# Keys the orchestrator dereferences without guarding. Validated once at
# startup so a typo in the JSON fails loudly here rather than on step 14 of a
# long run.
_REQUIRED: tuple[tuple[str, ...], ...] = (
    ("identity", "name"),
    ("model", "planner_model"),
    ("model", "max_tokens"),
    ("limits", "max_steps"),
    ("limits", "max_wall_clock_seconds"),
    ("limits", "max_tool_retries"),
    ("limits", "max_tokens_budget"),
    ("permissions", "default_tier"),
    ("permissions", "tool_tiers"),
    ("enabled_tools",),
)

_VALID_TIERS = frozenset({"auto", "confirm", "block"})


class ConfigError(RuntimeError):
    """Raised when `config/agent.json` is missing, malformed, or incomplete."""


def _dig(cfg: dict[str, Any], path: tuple[str, ...]) -> Any:
    node: Any = cfg
    for key in path:
        if not isinstance(node, dict) or key not in node:
            raise ConfigError(f"config is missing required key: {'.'.join(path)}")
        node = node[key]
    return node


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load, validate, and return the agent config.

    Resolution order for the path: explicit argument, then `$NEXUS_CONFIG`,
    then `config/agent.json`. Relative paths resolve against the project root,
    not the caller's cwd, so `python run.py` works from anywhere.
    """
    load_dotenv(PROJECT_ROOT / ".env")

    raw_path = Path(path or os.getenv("NEXUS_CONFIG") or _DEFAULT_CONFIG_PATH)
    resolved = raw_path if raw_path.is_absolute() else PROJECT_ROOT / raw_path

    try:
        cfg = json.loads(resolved.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {resolved}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config file is not valid JSON ({resolved}): {exc}") from exc

    if not isinstance(cfg, dict):
        raise ConfigError(f"config root must be a JSON object, got {type(cfg).__name__}")

    for required in _REQUIRED:
        _dig(cfg, required)

    if not isinstance(cfg["enabled_tools"], list) or not cfg["enabled_tools"]:
        raise ConfigError("config.enabled_tools must be a non-empty list")

    perms = cfg["permissions"]
    if perms["default_tier"] not in _VALID_TIERS:
        raise ConfigError(
            f"permissions.default_tier must be one of {sorted(_VALID_TIERS)}, "
            f"got {perms['default_tier']!r}"
        )
    for key, tier in perms["tool_tiers"].items():
        if tier not in _VALID_TIERS:
            raise ConfigError(
                f"permissions.tool_tiers[{key!r}] must be one of "
                f"{sorted(_VALID_TIERS)}, got {tier!r}"
            )

    limits = cfg["limits"]
    for key in ("max_steps", "max_wall_clock_seconds", "max_tokens_budget"):
        if not isinstance(limits[key], int) or limits[key] <= 0:
            raise ConfigError(f"limits.{key} must be a positive integer")
    if not isinstance(limits["max_tool_retries"], int) or limits["max_tool_retries"] < 0:
        raise ConfigError("limits.max_tool_retries must be a non-negative integer")

    cfg["_path"] = str(resolved)
    return cfg


def workspace_dir() -> Path:
    """The sandbox root that file_ops and code_exec are confined to.

    Created on demand. Everything the agent writes lands here and nowhere else.
    """
    raw = Path(os.getenv("NEXUS_WORKSPACE") or "workspace")
    root = raw if raw.is_absolute() else PROJECT_ROOT / raw
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()
