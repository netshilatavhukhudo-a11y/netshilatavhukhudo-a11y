"""Structured JSONL trace + pretty console output.

Every thought, tool call, tool result, and permission decision goes through
`trace()`. Two sinks, one call:

  * a machine-readable JSONL file (one event per line) under `traces/`
  * a human-readable `rich` rendering on stderr

Console output goes to stderr so `python run.py "goal" > answer.txt` captures
only the final answer.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from rich.console import Console
from rich.rule import Rule

from nexus import PROJECT_ROOT

__all__ = ["Tracer", "current_tracer", "start_run", "trace", "use_tracer"]

# How the console renders each event type: (label, style).
_STYLES: dict[str, tuple[str, str]] = {
    "run_start": ("RUN", "bold cyan"),
    "thought": ("THINK", "dim italic"),
    "tool_call": ("CALL", "bold yellow"),
    "tool_result": ("RESULT", "green"),
    "permission": ("PERM", "bold magenta"),
    "blocked": ("BLOCKED", "bold red"),
    "retry": ("RETRY", "yellow"),
    "error": ("ERROR", "bold red"),
    "llm_usage": ("TOKENS", "dim"),
    "final": ("FINAL", "bold green"),
    "run_end": ("END", "bold cyan"),
    "aborted": ("ABORT", "bold red"),
}

_CONSOLE_PREVIEW = 400


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _jsonable(value: Any) -> Any:
    """Coerce anything into something `json.dumps` accepts, losslessly if possible."""
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return repr(value)


class Tracer:
    """Writes one run's events to a JSONL file and to the console."""

    def __init__(self, run_id: str | None = None, trace_dir: Path | None = None) -> None:
        self.run_id = run_id or f"{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
        if trace_dir is None:
            raw = Path(os.getenv("NEXUS_TRACE_DIR") or "traces")
            trace_dir = raw if raw.is_absolute() else PROJECT_ROOT / raw
        trace_dir.mkdir(parents=True, exist_ok=True)
        self.path = trace_dir / f"{self.run_id}.jsonl"
        self.console = Console(stderr=True)
        self._lock = threading.Lock()
        self._started = time.monotonic()
        # Opened for the life of the run and flushed per event, so a hard kill
        # (SIGKILL, OOM) still leaves every event up to that point on disk.
        self._fh = self.path.open("a", encoding="utf-8")

    # -- writing -----------------------------------------------------------
    def write(self, event: str, data: dict[str, Any] | None = None) -> None:
        record = {
            "ts": _utcnow(),
            "run_id": self.run_id,
            "elapsed_s": round(time.monotonic() - self._started, 3),
            "event": event,
            "data": _jsonable(data or {}),
        }
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()
        self._render(event, record["data"])

    def _render(self, event: str, data: dict[str, Any]) -> None:
        label, style = _STYLES.get(event, (event.upper(), "white"))
        body = self._summarize(event, data)
        self.console.print(f"[{style}]{label:<8}[/] {body}", highlight=False, soft_wrap=True)

    @staticmethod
    def _summarize(event: str, data: dict[str, Any]) -> str:
        if event == "tool_call":
            args = json.dumps(data.get("input", {}), ensure_ascii=False)
            return f"{data.get('name')}({_clip(args, 200)})"
        if event == "tool_result":
            return f"{data.get('name')} -> {_clip(str(data.get('result', '')), _CONSOLE_PREVIEW)}"
        if event in {"thought", "final"}:
            key = "text" if event == "thought" else "answer"
            return _clip(str(data.get(key, "")), _CONSOLE_PREVIEW)
        if event == "permission":
            return f"{data.get('tool')} tier={data.get('tier')} -> {data.get('decision')}"
        if event == "llm_usage":
            return (
                f"in={data.get('input_tokens')} out={data.get('output_tokens')} "
                f"cumulative={data.get('cumulative')}/{data.get('budget')}"
            )
        return _clip(json.dumps(data, ensure_ascii=False), _CONSOLE_PREVIEW)

    # -- lifecycle ---------------------------------------------------------
    def rule(self, text: str) -> None:
        self.console.print(Rule(text, style="cyan"))

    def close(self) -> None:
        with self._lock:
            if not self._fh.closed:
                self._fh.close()


# --- module-level active tracer -------------------------------------------
# The orchestrator and every tool call `trace(...)` without threading a Tracer
# through their signatures. One run per process, so a module global is the
# honest representation.
_active: Tracer | None = None


def current_tracer() -> Tracer:
    """The active tracer, creating a default one if no run has started."""
    global _active
    if _active is None:
        _active = Tracer()
    return _active


def start_run(goal: str, config: dict[str, Any]) -> Tracer:
    """Begin a new run: rotate in a fresh tracer and log the opening event."""
    global _active
    if _active is not None:
        _active.close()
    _active = Tracer()
    _active.rule(f"{config.get('identity', {}).get('name', 'NEXUS')} · {_active.run_id}")
    _active.write(
        "run_start",
        {
            "goal": goal,
            "planner_model": config["model"]["planner_model"],
            "enabled_tools": config["enabled_tools"],
            "limits": config["limits"],
            "trace_file": str(_active.path),
        },
    )
    return _active


def trace(event: str, data: dict[str, Any] | None = None) -> None:
    """Record one event to the active run's trace."""
    current_tracer().write(event, data)


class use_tracer:
    """Context manager that swaps in a specific tracer. Used by the test suite."""

    def __init__(self, tracer: Tracer) -> None:
        self._new = tracer
        self._prev: Tracer | None = None

    def __enter__(self) -> Tracer:
        global _active
        self._prev = _active
        _active = self._new
        return self._new

    def __exit__(self, *exc: object) -> None:
        global _active
        self._new.close()
        _active = self._prev


def read_trace(path: str | Path) -> Iterator[dict[str, Any]]:
    """Replay a trace file. Handy for post-mortems and for the test suite."""
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + f"… (+{len(text) - limit} chars)"
