"""Tool discovery and JSON-schema export.

`load_tools()` imports the modules named in `enabled_tools`, which triggers
their `@tool` decorators and fills `REGISTRY`. `export_schemas()` hands the
resulting list to the LLM on every turn.
"""

from __future__ import annotations

import importlib
from typing import Any, Iterable

from nexus.tools.base import REGISTRY, Tool

__all__ = ["export_schemas", "get_tool", "load_tools", "loaded_tool_names"]


class ToolLoadError(RuntimeError):
    """A tool named in `enabled_tools` could not be imported or registered."""


def load_tools(enabled: Iterable[str]) -> tuple[list[str], dict[str, str]]:
    """Import every enabled tool module.

    Returns `(loaded, skipped)` where `skipped` maps tool name -> reason.

    A tool whose optional dependency is missing (Playwright, say) is skipped
    with a reason rather than taking the whole agent down — the model simply
    never sees that tool and is told, via its system prompt, to say plainly
    when it lacks a capability. A tool that exists but is broken still raises.
    """
    loaded: list[str] = []
    skipped: dict[str, str] = {}

    for name in enabled:
        try:
            importlib.import_module(f"nexus.tools.{name}")
        except ModuleNotFoundError as exc:
            missing = getattr(exc, "name", "") or ""
            # A missing `nexus.tools.<name>` is a config error and must surface;
            # a missing third-party package is a degraded-capability warning.
            if missing == f"nexus.tools.{name}":
                raise ToolLoadError(
                    f"enabled_tools lists {name!r} but nexus/tools/{name}.py does not exist"
                ) from exc
            skipped[name] = f"missing dependency {missing!r} (pip install -r requirements.txt)"
            continue
        except Exception as exc:  # noqa: BLE001 - surfaced verbatim below
            raise ToolLoadError(f"failed to import tool {name!r}: {exc}") from exc

        if name not in REGISTRY:
            raise ToolLoadError(
                f"nexus/tools/{name}.py imported but registered no tool named {name!r} "
                f"— check the @tool(...) name argument"
            )
        loaded.append(name)

    return loaded, skipped


def export_schemas(enabled: Iterable[str]) -> list[dict[str, Any]]:
    """The `tools` payload for the Messages API, in stable (config) order."""
    return [REGISTRY[name].schema() for name in enabled if name in REGISTRY]


def get_tool(name: str) -> Tool:
    try:
        return REGISTRY[name]
    except KeyError:
        raise ToolLoadError(
            f"unknown tool {name!r}; registered: {sorted(REGISTRY)}"
        ) from None


def loaded_tool_names() -> list[str]:
    return sorted(REGISTRY)
