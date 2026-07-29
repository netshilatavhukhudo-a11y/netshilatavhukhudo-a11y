"""The agent loop: perceive -> plan -> act -> observe -> reflect.

This module is deliberately generic. It knows about four things — the LLM, the
tool registry, the permission gate, and the trace — and nothing about what any
individual tool does. Domain logic belongs in `tools/`, never here.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from nexus.llm import BudgetExceeded, LLMError, TokenBudget, call_llm, text_of
from nexus.permissions import authorize
from nexus.registry import export_schemas, get_tool, load_tools
from nexus.tools.base import ToolError
from nexus.tracing import trace

__all__ = ["RunResult", "run"]

SYSTEM_PROMPT = """You are NEXUS, an autonomous agent.
You accomplish the user's GOAL by thinking step by step and calling tools.
Rules:
- Break the goal into concrete sub-steps. Do one tool call at a time.
- After each tool result, decide the next action. Re-plan if a step fails.
- Verify your work before declaring the goal complete.
- If you lack a capability, say so plainly instead of pretending.
- Never fabricate tool results or URLs.
When the goal is fully achieved, stop calling tools and give a final summary."""

_RETRY_BASE_DELAY = 0.5


@dataclass
class RunResult:
    """Everything the caller needs to know about a finished run."""

    answer: str
    status: str  # done | max_steps | timeout | budget | aborted | error | refused
    steps: int
    elapsed_s: float
    usage: dict[str, Any] = field(default_factory=dict)
    trace_file: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "done"


def _build_system_prompt(config: dict[str, Any], skipped: dict[str, str]) -> str:
    identity = config.get("identity", {})
    parts = [SYSTEM_PROMPT]

    name = identity.get("name")
    role = identity.get("role")
    operator = identity.get("operator")
    if name or role or operator:
        bits = [f"You are {name}." if name else "", role or "", f"Your operator is {operator}." if operator else ""]
        parts.append("\n".join(b for b in bits if b))

    tiers = config["permissions"]["tool_tiers"]
    gated = sorted(k for k, v in tiers.items() if v in {"confirm", "block"})
    if gated:
        parts.append(
            "Some actions require human approval before they run: "
            + ", ".join(gated)
            + ". If an action is blocked, do not try to route around it — report it "
            "and continue with what you can do."
        )

    if skipped:
        parts.append(
            "These capabilities are unavailable in this environment: "
            + "; ".join(f"{k} ({v})" for k, v in skipped.items())
            + ". Say so plainly if the goal needs them."
        )
    return "\n\n".join(parts)


def _execute(name: str, tool_input: dict[str, Any], max_retries: int) -> str:
    """Run one tool, retrying transient failures. Returns text for the model."""
    handler = get_tool(name).handler
    last_error = ""

    for attempt in range(max_retries + 1):
        try:
            return str(handler(tool_input))
        except ToolError as exc:
            last_error = f"[ERROR] {exc}"
        except Exception as exc:  # noqa: BLE001 - the model gets to re-plan around it
            last_error = f"[ERROR] {type(exc).__name__}: {exc}"

        if attempt < max_retries:
            delay = _RETRY_BASE_DELAY * (2**attempt)
            trace("retry", {"tool": name, "attempt": attempt + 1, "delay_s": delay, "error": last_error})
            time.sleep(delay)

    return last_error


def run(goal: str, config: dict[str, Any]) -> RunResult:
    """Pursue `goal` until it is met or a stop condition fires."""
    started = time.monotonic()
    limits = config["limits"]
    model_cfg = config["model"]

    loaded, skipped = load_tools(config["enabled_tools"])
    if skipped:
        trace("error", {"unavailable_tools": skipped})

    tools = export_schemas(loaded)
    system = _build_system_prompt(config, skipped)
    budget = TokenBudget(limit=limits["max_tokens_budget"])
    max_result_chars = limits.get("max_tool_result_chars", 20000)

    messages: list[dict[str, Any]] = [{"role": "user", "content": f"GOAL: {goal}"}]
    steps = 0
    status = "max_steps"
    answer = "[STOPPED] Max steps reached without completing the goal."

    try:
        while steps < limits["max_steps"]:
            elapsed = time.monotonic() - started
            if elapsed > limits["max_wall_clock_seconds"]:
                status = "timeout"
                answer = (
                    f"[STOPPED] Wall-clock limit of {limits['max_wall_clock_seconds']}s "
                    f"reached after {steps} steps."
                )
                break

            steps += 1
            trace("thought", {"text": f"step {steps}/{limits['max_steps']}"})

            response = call_llm(
                model=model_cfg["planner_model"],
                system=system,
                messages=messages,
                tools=tools,
                max_tokens=model_cfg["max_tokens"],
                effort=model_cfg.get("effort"),
                thinking=model_cfg.get("thinking"),
                budget=budget,
                prompt_caching=model_cfg.get("prompt_caching", True),
            )

            # Persist the assistant turn verbatim — text, thinking, and
            # tool_use blocks alike. Dropping any of them makes the next
            # request invalid.
            messages.append({"role": "assistant", "content": response.content})

            for block in response.content:
                if getattr(block, "type", None) == "thinking" and getattr(block, "thinking", ""):
                    trace("thought", {"text": block.thinking})

            stop_reason = getattr(response, "stop_reason", None)

            if stop_reason == "refusal":
                details = getattr(response, "stop_details", None)
                status = "refused"
                answer = (
                    "[REFUSED] The model declined this request"
                    + (f" (category: {getattr(details, 'category', None)})" if details else "")
                    + ". Rephrase the goal or narrow its scope."
                )
                break

            if stop_reason != "tool_use":
                answer = text_of(response.content)
                if stop_reason == "max_tokens":
                    status = "error"
                    answer = (answer or "") + (
                        "\n\n[TRUNCATED] Hit max_tokens mid-response. Raise "
                        "model.max_tokens in config/agent.json."
                    )
                else:
                    status = "done"
                trace("final", {"answer": answer, "steps": steps, "stop_reason": stop_reason})
                break

            tool_uses = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
            if not tool_uses:
                status = "error"
                answer = "[ERROR] Model signalled tool_use but returned no tool_use block."
                break

            tool_results: list[dict[str, Any]] = []
            for block in tool_uses:
                tool_input = dict(block.input or {})
                trace("tool_call", {"name": block.name, "input": tool_input, "id": block.id})

                allowed, reason = authorize(block.name, tool_input, config)
                trace(
                    "permission",
                    {
                        "tool": block.name,
                        "tier": reason.tier,
                        "decision": "allow" if allowed else "deny",
                        "reason": reason.detail,
                    },
                )

                if not allowed:
                    result = f"[BLOCKED] {reason.detail}. Action not performed."
                    trace("blocked", {"name": block.name, "input": tool_input, "reason": reason.detail})
                else:
                    result = _execute(block.name, tool_input, limits["max_tool_retries"])

                if len(result) > max_result_chars:
                    result = result[:max_result_chars] + f"\n[truncated at {max_result_chars} chars]"

                trace("tool_result", {"name": block.name, "result": result[:2000], "id": block.id})
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": result}
                )

            # One user turn carrying every tool_result, one per tool_use_id.
            messages.append({"role": "user", "content": tool_results})

    except KeyboardInterrupt:
        status = "aborted"
        answer = f"[ABORTED] Interrupted by the operator after {steps} steps."
        trace("aborted", {"steps": steps})
    except BudgetExceeded as exc:
        status = "budget"
        answer = f"[STOPPED] {exc}"
        trace("error", {"budget": str(exc)})
    except LLMError as exc:
        status = "error"
        answer = f"[ERROR] {exc}"
        trace("error", {"llm": str(exc)})

    result = RunResult(
        answer=answer,
        status=status,
        steps=steps,
        elapsed_s=round(time.monotonic() - started, 2),
        usage=budget.summary(),
    )
    trace(
        "run_end",
        {"status": result.status, "steps": result.steps, "elapsed_s": result.elapsed_s, "usage": result.usage},
    )
    return result
