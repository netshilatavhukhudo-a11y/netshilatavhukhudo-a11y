"""Persist or recall facts across steps and sessions.

Thin wrapper over `nexus.memory.Memory`. One process-wide store, opened
lazily, so every step in a run — and every future run in the same workspace —
sees the same facts.
"""

from __future__ import annotations

from nexus.memory import Memory
from nexus.tools.base import ToolError, tool

_SCHEMA = {
    "type": "object",
    "properties": {
        "op": {"type": "string", "enum": ["save", "recall", "search"]},
        "key": {"type": "string"},
        "value": {"type": "string"},
        "query": {"type": "string"},
    },
    "required": ["op"],
}

_store: Memory | None = None


def _memory() -> Memory:
    global _store
    if _store is None:
        _store = Memory()
    return _store


@tool(
    "memory_tool",
    "Persist or recall facts across steps and sessions. Use to remember "
    "goals, findings, and intermediate results.",
    _SCHEMA,
    tier_key="memory_tool",
)
def memory_tool(inp: dict) -> str:
    op = (inp.get("op") or "").strip().lower()
    mem = _memory()

    if op == "save":
        key, value = inp.get("key"), inp.get("value")
        if not key or not str(key).strip():
            raise ToolError("op='save' requires a non-empty 'key'")
        if value is None:
            raise ToolError("op='save' requires a 'value'")
        mem.save(str(key), str(value))
        return f"Saved {str(key).strip()!r} ({len(str(value))} chars)."

    if op == "recall":
        key = inp.get("key")
        if not key or not str(key).strip():
            raise ToolError("op='recall' requires a 'key'")
        value = mem.recall(str(key))
        if value is None:
            return f"No memory stored under {str(key).strip()!r}."
        return value

    if op == "search":
        query = inp.get("query")
        if not query or not str(query).strip():
            raise ToolError("op='search' requires a 'query'")
        hits = mem.search(str(query))
        if not hits:
            return f"No memories match {str(query).strip()!r}."
        return f"{len(hits)} match(es) for {str(query).strip()!r}:\n" + "\n---\n".join(hits)

    raise ToolError(f"unknown op {op!r}; expected save, recall, or search")
