"""Read, write, list, or delete files in the agent workspace.

Every path is resolved and then checked against the workspace root, so the
tool cannot escape it. `.resolve()` collapses `..` *and* follows symlinks
before the check, which closes both the `../../etc/passwd` route and the
"symlink inside the workspace pointing out of it" route.
"""

from __future__ import annotations

from pathlib import Path

from nexus import workspace_dir
from nexus.tools.base import ToolError, tool

_SCHEMA = {
    "type": "object",
    "properties": {
        "op": {"type": "string", "enum": ["read", "write", "list", "delete"]},
        "path": {"type": "string"},
        "content": {"type": "string"},
    },
    "required": ["op", "path"],
}

_MAX_READ_CHARS = 20000
_MAX_LIST_ENTRIES = 500


def resolve_in_workspace(path: str) -> Path:
    """Resolve `path` inside the workspace, or raise.

    Returned paths are always real, absolute, and provably under the root.
    """
    root = workspace_dir()
    raw = (path or "").strip()
    if not raw:
        raise ToolError("file_ops requires a non-empty 'path'")

    candidate = Path(raw)
    target = (candidate if candidate.is_absolute() else root / candidate)

    # strict=False so we can resolve paths that do not exist yet (writes).
    resolved = target.resolve(strict=False)
    if resolved != root and root not in resolved.parents:
        raise ToolError(
            f"path {raw!r} resolves to {resolved}, which is outside the workspace "
            f"({root}). file_ops cannot read or write outside the sandbox."
        )
    return resolved


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(workspace_dir()))
    except ValueError:
        return str(path)


@tool(
    "file_ops",
    "Read, write, list, or delete files in the agent workspace. Paths are "
    "relative to the workspace root and cannot escape it.",
    _SCHEMA,
    tier_key="file_ops",
)
def file_ops(inp: dict) -> str:
    op = (inp.get("op") or "").strip().lower()
    if op not in {"read", "write", "list", "delete"}:
        raise ToolError(f"unknown op {op!r}; expected read, write, list, or delete")

    target = resolve_in_workspace(inp.get("path", ""))

    if op == "read":
        if not target.exists():
            raise ToolError(f"no such file: {_rel(target)}")
        if target.is_dir():
            raise ToolError(f"{_rel(target)} is a directory — use op='list'")
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ToolError(f"could not read {_rel(target)}: {exc}") from exc
        if len(text) > _MAX_READ_CHARS:
            return text[:_MAX_READ_CHARS] + f"\n[truncated at {_MAX_READ_CHARS} chars]"
        return text or "[file is empty]"

    if op == "write":
        content = inp.get("content")
        if content is None:
            raise ToolError("op='write' requires 'content'")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(content), encoding="utf-8")
        except OSError as exc:
            raise ToolError(f"could not write {_rel(target)}: {exc}") from exc
        return f"Wrote {len(str(content))} chars to {_rel(target)}"

    if op == "list":
        if not target.exists():
            raise ToolError(f"no such directory: {_rel(target)}")
        if target.is_file():
            stat = target.stat()
            return f"{_rel(target)} (file, {stat.st_size} bytes)"
        entries = []
        for child in sorted(target.rglob("*"))[:_MAX_LIST_ENTRIES]:
            kind = "dir " if child.is_dir() else "file"
            size = "" if child.is_dir() else f" {child.stat().st_size}b"
            entries.append(f"  {kind} {_rel(child)}{size}")
        if not entries:
            return f"{_rel(target)} is empty"
        return f"{_rel(target)}:\n" + "\n".join(entries)

    # delete — block tier, so a human has already seen this exact path.
    if not target.exists():
        raise ToolError(f"no such path: {_rel(target)}")
    if target == workspace_dir():
        raise ToolError("refusing to delete the workspace root itself")
    try:
        if target.is_dir():
            # One typed approval should not be able to erase a whole tree.
            # Deleting a populated directory means deleting its files first,
            # each one shown to and approved by the operator.
            if any(target.iterdir()):
                raise ToolError(
                    f"{_rel(target)} is not empty. Delete its contents first — "
                    f"one approval does not cover a recursive delete."
                )
            target.rmdir()
        else:
            target.unlink()
    except OSError as exc:
        raise ToolError(f"could not delete {_rel(target)}: {exc}") from exc
    return f"Deleted {_rel(target)}"
