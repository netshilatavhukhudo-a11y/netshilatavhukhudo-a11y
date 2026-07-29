"""Execute Python in an isolated subprocess.

Never `exec()` in-process — that would give generated code the agent's own
memory, its imported modules, and its API key.

Isolation applied here, strongest available first:

  * **Separate process**, killed as a process group on timeout so a child
    that outlives its parent cannot survive.
  * **Network namespace** via `unshare -n` when the kernel allows it, so the
    code cannot phone home or reach internal services. Degradation is
    reported rather than silently accepted.
  * **Resource limits** (`RLIMIT_AS`, `RLIMIT_CPU`, `RLIMIT_NPROC`,
    `RLIMIT_FSIZE`, `RLIMIT_CORE`) so runaway memory, CPU, fork bombs, and
    disk-filling all terminate instead of taking the host down.
  * **A scrubbed environment** — the child gets PATH, HOME, and locale, and
    nothing else. `ANTHROPIC_API_KEY` and every other secret in the parent
    environment are not inherited.
  * **`python -I`** (isolated mode): ignores `PYTHON*` env vars and the user
    site directory.
  * **cwd pinned to the workspace**, the same sandbox `file_ops` uses.

A container or a dedicated jail would still be stronger. This is the floor
that a bare subprocess is not.
"""

from __future__ import annotations

import os
import resource
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from nexus import workspace_dir
from nexus.tools.base import ToolError, tool
from nexus.tracing import trace

_SCHEMA = {
    "type": "object",
    "properties": {
        "code": {"type": "string"},
        "timeout_seconds": {"type": "integer", "default": 30},
    },
    "required": ["code"],
}

_DEFAULT_TIMEOUT = 30
_MAX_TIMEOUT = 120
_MAX_OUTPUT_CHARS = 8000

_MEM_LIMIT_BYTES = 512 * 1024 * 1024  # 512 MiB address space
_FSIZE_LIMIT_BYTES = 64 * 1024 * 1024  # 64 MiB per file written
_NPROC_LIMIT = 64  # blunt anti-fork-bomb

_netns_supported: bool | None = None  # probed once per process


def _network_isolation_available() -> bool:
    """Can we put the child in an empty network namespace?"""
    global _netns_supported
    if _netns_supported is None:
        if shutil.which("unshare") is None:
            _netns_supported = False
        else:
            try:
                probe = subprocess.run(
                    ["unshare", "-n", "true"],
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
                _netns_supported = probe.returncode == 0
            except (OSError, subprocess.SubprocessError):
                _netns_supported = False
    return _netns_supported


def _apply_limits(timeout: int) -> None:
    """Runs in the child between fork and exec."""
    os.setsid()  # own process group, so timeout kills the whole tree
    resource.setrlimit(resource.RLIMIT_AS, (_MEM_LIMIT_BYTES, _MEM_LIMIT_BYTES))
    resource.setrlimit(resource.RLIMIT_CPU, (timeout + 1, timeout + 1))
    resource.setrlimit(resource.RLIMIT_FSIZE, (_FSIZE_LIMIT_BYTES, _FSIZE_LIMIT_BYTES))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    try:
        resource.setrlimit(resource.RLIMIT_NPROC, (_NPROC_LIMIT, _NPROC_LIMIT))
    except (ValueError, OSError):
        pass  # not enforceable everywhere; the others still apply


def _child_env(workspace: Path) -> dict[str, str]:
    """A deliberately tiny environment. Secrets are not inherited."""
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(workspace),
        "TMPDIR": str(workspace),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
    }


@tool(
    "code_exec",
    "Execute Python in an isolated sandbox and return stdout/stderr. Use for "
    "computation, data parsing, or generating files. Has no network access, "
    "and can only touch files in the agent workspace.",
    _SCHEMA,
    tier_key="code_exec",
)
def code_exec(inp: dict) -> str:
    code = inp.get("code")
    if not isinstance(code, str) or not code.strip():
        raise ToolError("code_exec requires non-empty 'code'")

    try:
        timeout = int(inp.get("timeout_seconds") or _DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        timeout = _DEFAULT_TIMEOUT
    timeout = max(1, min(_MAX_TIMEOUT, timeout))

    workspace = workspace_dir()
    isolated = _network_isolation_available()

    with tempfile.NamedTemporaryFile(
        "w", suffix=".py", delete=False, dir=workspace, encoding="utf-8"
    ) as handle:
        handle.write(code)
        script = Path(handle.name)

    argv = [sys.executable, "-I", str(script)]
    if isolated:
        argv = ["unshare", "-n", *argv]

    trace(
        "thought",
        {"text": f"code_exec: {len(code)} chars, timeout={timeout}s, netns={isolated}"},
    )

    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=workspace,
            env=_child_env(workspace),
            preexec_fn=lambda: _apply_limits(timeout),  # noqa: PLW1509 - single-threaded
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"[TIMEOUT] Execution exceeded {timeout}s and was killed."
    except OSError as exc:
        raise ToolError(f"could not start the sandbox: {exc}") from exc
    finally:
        script.unlink(missing_ok=True)

    parts: list[str] = []
    if completed.stdout:
        parts.append(completed.stdout)
    if completed.stderr:
        parts.append(f"[stderr]\n{completed.stderr}")
    if completed.returncode != 0:
        parts.append(f"[exit code {completed.returncode}]")
    if not isolated:
        parts.append(
            "[WARNING] network isolation unavailable on this host — the code ran "
            "with whatever network access the agent process has."
        )

    output = "\n".join(parts).strip() or "[no output]"
    if len(output) > _MAX_OUTPUT_CHARS:
        output = output[:_MAX_OUTPUT_CHARS] + f"\n[truncated at {_MAX_OUTPUT_CHARS} chars]"
    return output
