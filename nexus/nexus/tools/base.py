"""The Tool contract.

A tool is defined once, in one file, and its wire schema is generated from
that definition. Adding a capability to NEXUS means:

    1. write `nexus/tools/<name>.py` and decorate the handler with `@tool`
    2. add `"<name>"` to `enabled_tools` in `config/agent.json`
    3. add a tier for it under `permissions.tool_tiers`

Nothing in the core loop changes. That is the entire extension mechanism.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

__all__ = ["REGISTRY", "Tool", "ToolError", "tool"]

Handler = Callable[[dict[str, Any]], str]

# Populated at import time by the `@tool` decorator. `registry.load_tools()`
# imports the modules named in `enabled_tools`, which is what fills this.
REGISTRY: dict[str, "Tool"] = {}


class ToolError(RuntimeError):
    """A tool failed in a way the agent should see and can plan around.

    Raise this (rather than letting an arbitrary exception escape) when the
    failure is expected and actionable — a bad URL, a missing file, an HTTP
    404. The orchestrator turns it into a readable tool_result instead of a
    stack trace, so the model can re-plan.
    """


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]  # JSON Schema
    handler: Handler  # (input: dict) -> str
    tier_key: str  # maps into permissions.tool_tiers

    def schema(self) -> dict[str, Any]:
        """Exactly the shape the Anthropic Messages API expects in `tools`."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


def tool(
    name: str,
    description: str,
    input_schema: dict[str, Any],
    tier_key: str | None = None,
) -> Callable[[Handler], Handler]:
    """Register a function as a tool. Returns the function unchanged.

    `tier_key` defaults to `name`; pass it explicitly only when a tool's
    permission tier is looked up under a different key (see permissions.py for
    how `http_request` resolves to `http_request.POST`).
    """

    def deco(fn: Handler) -> Handler:
        if name in REGISTRY and REGISTRY[name].handler is not fn:
            raise ValueError(f"duplicate tool registration: {name!r}")
        REGISTRY[name] = Tool(
            name=name,
            description=description,
            input_schema=input_schema,
            handler=fn,
            tier_key=tier_key or name,
        )
        return fn

    return deco
