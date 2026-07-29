"""Guardrails: permission tiers and the human approval gate.

This is what makes an autonomous agent trustworthy instead of a loaded gun.

Invariants that hold no matter what the model outputs:

  * **Tiers come from config, never from the model.** The model's tool input
    only ever *selects among* keys the operator already declared.
  * **The model cannot downgrade a tier.** When several config keys match a
    call, the strictest one wins — so adding a narrower key can never weaken
    a broader rule, and neither can a creatively-shaped tool input.
  * **`block` requires a typed confirmation every single time.** There is no
    "approve all", no session memory, no `--yes` flag. By design.
  * **Every blocked-but-attempted action is logged** to the trace.
"""

from __future__ import annotations

import fnmatch
import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Callable

from nexus.tracing import trace

__all__ = ["Decision", "authorize", "resolve_tier"]

# Strictness order. Used to pick a winner when several config keys match.
_RANK = {"auto": 0, "confirm": 1, "block": 2}

_CONFIRM_WORDS = frozenset({"y", "yes"})
_BLOCK_PHRASE = "APPROVE"  # case-sensitive, typed in full, every time


@dataclass(frozen=True)
class Decision:
    tier: str
    detail: str
    matched_keys: tuple[str, ...] = ()


def _browser_subkey(action: str) -> str:
    """Reading a page is not the same act as driving a form."""
    return "submit_form" if action in {"click", "fill", "press", "select"} else "read"


# tool name -> (input field that discriminates, normaliser)
_DISCRIMINATORS: dict[str, tuple[str, Callable[[str], str]]] = {
    "http_request": ("method", lambda v: v.strip().upper()),
    "file_ops": ("op", lambda v: v.strip().lower()),
    "memory_tool": ("op", lambda v: v.strip().lower()),
    "browser": ("action", lambda v: _browser_subkey(v.strip().lower())),
}


def _strictest(tiers: list[str]) -> str:
    return max(tiers, key=lambda t: _RANK.get(t, _RANK["block"]))


def resolve_tier(tool_name: str, tool_input: dict[str, Any], config: dict[str, Any]) -> Decision:
    """Map a concrete tool call onto a permission tier.

    Matching collects every applicable key — the exact sub-action key, the
    bare tool name, and any `prefix.*` wildcard — then returns the strictest
    tier among them.
    """
    perms = config["permissions"]
    table: dict[str, str] = perms["tool_tiers"]

    candidates: list[str] = [tool_name]
    subkey: str | None = None

    if tool_name in _DISCRIMINATORS:
        field, normalise = _DISCRIMINATORS[tool_name]
        raw = tool_input.get(field)
        if isinstance(raw, str) and raw.strip():
            subkey = normalise(raw)
            candidates.append(f"{tool_name}.{subkey}")

    matched: dict[str, str] = {}
    for key, tier in table.items():
        if key in candidates:
            matched[key] = tier
        elif key.endswith(".*") and any(fnmatch.fnmatch(c, key) for c in candidates):
            matched[key] = tier

    if matched:
        return Decision(
            tier=_strictest(list(matched.values())),
            detail=f"tier from config keys {sorted(matched)}",
            matched_keys=tuple(sorted(matched)),
        )

    # A tool with a declared discriminator whose value we could not read (or
    # did not recognise) falls back to the strictest tier configured anywhere
    # under that tool's prefix. Otherwise "file_ops with no op" would slip
    # through at the default tier and side-step file_ops.delete = block.
    if tool_name in _DISCRIMINATORS:
        prefix_tiers = [t for k, t in table.items() if k.split(".")[0] == tool_name]
        if prefix_tiers:
            strict = _strictest(prefix_tiers)
            return Decision(
                tier=strict,
                detail=(
                    f"{tool_name} called with unrecognised "
                    f"{_DISCRIMINATORS[tool_name][0]}={tool_input.get(_DISCRIMINATORS[tool_name][0])!r}; "
                    f"falling back to strictest configured tier for {tool_name}.*"
                ),
            )

    return Decision(
        tier=perms["default_tier"],
        detail=f"no tool_tiers entry for {tool_name!r}; using default_tier",
    )


def _noninteractive_reason() -> str | None:
    """Why we cannot prompt, or None if we can."""
    if os.getenv("NEXUS_NONINTERACTIVE") == "1":
        return "NEXUS_NONINTERACTIVE=1"
    if not sys.stdin or not sys.stdin.isatty():
        return "no interactive terminal on stdin"
    return None


def _render_request(tool_name: str, tool_input: dict[str, Any], tier: str, loud: bool) -> str:
    body = json.dumps(tool_input, indent=2, ensure_ascii=False, default=str)
    bar = "!" * 72 if loud else "-" * 72
    heading = (
        "IRREVERSIBLE ACTION — EXPLICIT APPROVAL REQUIRED"
        if loud
        else "APPROVAL REQUIRED"
    )
    return f"\n{bar}\n{heading}\ntier : {tier}\ntool : {tool_name}\ninput:\n{body}\n{bar}"


def human_gate(
    tool_name: str,
    tool_input: dict[str, Any],
    tier: str,
    *,
    typed: str,
    loud: bool = False,
) -> tuple[bool, str]:
    """Ask a human. Returns `(allowed, detail)`.

    Fails closed: if there is no terminal to ask on, the answer is no.
    """
    blocked_reason = _noninteractive_reason()
    if blocked_reason is not None:
        detail = f"{tier}-tier action needs human approval but none could be requested ({blocked_reason})"
        trace("blocked", {"tool": tool_name, "input": tool_input, "tier": tier, "reason": detail})
        return False, detail

    prompt_hint = (
        f"type exactly {typed!r} to approve, anything else to deny: "
        if loud
        else f"approve? [{'/'.join(sorted(_CONFIRM_WORDS))}/N]: "
    )
    print(_render_request(tool_name, tool_input, tier, loud), file=sys.stderr)

    try:
        answer = input(prompt_hint)
    except EOFError:
        detail = "stdin closed while awaiting approval"
        trace("blocked", {"tool": tool_name, "input": tool_input, "tier": tier, "reason": detail})
        return False, detail
    # KeyboardInterrupt deliberately propagates: Ctrl-C at an approval prompt
    # is the kill switch, and it should abort the run, not just deny one call.

    approved = (answer == typed) if loud else (answer.strip().lower() in _CONFIRM_WORDS)

    if not approved:
        detail = f"operator denied {tier}-tier action (answer was {answer.strip()[:40]!r})"
        trace("blocked", {"tool": tool_name, "input": tool_input, "tier": tier, "reason": detail})
        return False, detail

    return True, f"operator approved {tier}-tier action"


def authorize(
    tool_name: str, tool_input: dict[str, Any], config: dict[str, Any]
) -> tuple[bool, Decision]:
    """The gate the orchestrator calls before every single tool execution."""
    decision = resolve_tier(tool_name, tool_input, config)

    if decision.tier == "auto":
        return True, Decision(decision.tier, "reversible/read — no approval needed", decision.matched_keys)

    if decision.tier == "confirm":
        allowed, detail = human_gate(tool_name, tool_input, "confirm", typed="y")
        return allowed, Decision(decision.tier, detail, decision.matched_keys)

    if decision.tier == "block":
        # Money movement, deletes, live-price changes. Full detail on screen
        # and a typed phrase, every time — no batching, no remembering.
        allowed, detail = human_gate(
            tool_name, tool_input, "block", typed=_BLOCK_PHRASE, loud=True
        )
        return allowed, Decision(decision.tier, detail, decision.matched_keys)

    return False, Decision(decision.tier, f"unknown tier {decision.tier!r} — refusing", decision.matched_keys)
