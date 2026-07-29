"""Thin wrapper over the Anthropic Messages API.

Swapping the brain is one config value (`model.planner_model`). Nothing above
this module knows which model is in play.

Notes on the current API surface, verified against docs.claude.com — these are
not stylistic choices, they are what the endpoint accepts:

  * `temperature`, `top_p`, and `top_k` are **removed** on the current planner
    model and return a 400. Reasoning depth is steered with
    `output_config.effort` instead (`low` … `max`).
  * Extended thinking with a fixed `budget_tokens` is likewise removed. The
    supported form is `thinking={"type": "adaptive"}`, which is also the
    default — so `max_tokens` must leave room for thinking *plus* the answer.
  * Safety classifiers can decline a request: HTTP 200 with
    `stop_reason == "refusal"` and an empty/partial `content`. Callers must
    check `stop_reason` before reading `content`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Iterable

import anthropic

from nexus.tracing import trace

__all__ = [
    "BudgetExceeded",
    "LLMError",
    "TokenBudget",
    "call_llm",
    "get_client",
    "text_of",
]

# Opting into server-side refusal fallbacks: when a safety classifier declines
# a request, the API re-serves it on Anthropic's recommended fallback model
# inside the same call instead of handing back an unusable refusal.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

# Flipped off permanently for the process if the API rejects the beta, so a
# rollout gap costs one wasted request rather than every request.
_fallback_enabled = True


class LLMError(RuntimeError):
    """The model call failed in a way retrying will not fix."""


class BudgetExceeded(RuntimeError):
    """The run consumed its whole token budget."""


@dataclass
class TokenBudget:
    """Cumulative token accounting for one run."""

    limit: int
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    _by_model: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.total)

    def check(self) -> None:
        """Raise before spending anything more if the budget is already gone."""
        if self.total >= self.limit:
            raise BudgetExceeded(
                f"token budget exhausted: {self.total}/{self.limit} across {self.calls} calls"
            )

    def record(self, model: str, usage: Any) -> None:
        self.calls += 1
        # Cache reads/writes are billed input; count them so the budget tracks
        # spend rather than a subset of it.
        self.input_tokens += (
            int(getattr(usage, "input_tokens", 0) or 0)
            + int(getattr(usage, "cache_read_input_tokens", 0) or 0)
            + int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        )
        self.output_tokens += int(getattr(usage, "output_tokens", 0) or 0)
        self._by_model[model] = self._by_model.get(model, 0) + 1

    def summary(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total": self.total,
            "limit": self.limit,
            "calls": self.calls,
            "by_model": dict(self._by_model),
        }


_client: anthropic.Anthropic | None = None


def get_client(max_retries: int = 2) -> anthropic.Anthropic:
    """The shared SDK client.

    The key comes from `ANTHROPIC_API_KEY` (loaded from `.env`) — never from
    an argument, a config file, or source. The SDK's own retry handles 429s,
    5xx, and connection errors with exponential backoff.
    """
    global _client
    if _client is None:
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise LLMError(
                "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and fill it in."
            )
        _client = anthropic.Anthropic(max_retries=max_retries)
    return _client


def call_llm(
    *,
    model: str,
    system: str,
    messages: list[dict[str, Any]],
    tools: Iterable[dict[str, Any]] | None = None,
    max_tokens: int = 16000,
    effort: str | None = "high",
    thinking: str | None = "adaptive",
    budget: TokenBudget | None = None,
    client: Any | None = None,
    prompt_caching: bool = True,
) -> Any:
    """One turn of the conversation. Returns the raw SDK response.

    Raises `BudgetExceeded` before the call if the run is already out of
    tokens, and `LLMError` for non-retryable API failures.
    """
    global _fallback_enabled

    if budget is not None:
        budget.check()

    api = client if client is not None else get_client()

    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
    }
    if tools:
        kwargs["tools"] = list(tools)
    if thinking:
        # `display: summarized` opts back into readable reasoning; the default
        # is "omitted", which streams thinking blocks with empty text.
        kwargs["thinking"] = {"type": thinking, "display": "summarized"}
    if effort:
        kwargs["output_config"] = {"effort": effort}
    if prompt_caching:
        # Auto-places the breakpoint on the last cacheable block, i.e. the end
        # of the most recently appended turn. Tools and system sit in the
        # prefix ahead of it, so they ride the same cache entry.
        kwargs["cache_control"] = {"type": "ephemeral"}

    try:
        response = _create(api, kwargs)
    except anthropic.BadRequestError as exc:
        if _fallback_enabled and _looks_like_fallback_rejection(exc):
            # The refusal-fallback beta is not available here. Drop it for the
            # rest of the process and retry once on the stable endpoint.
            _fallback_enabled = False
            trace("retry", {"reason": "refusal-fallback beta unavailable", "detail": str(exc)[:200]})
            response = _create(api, kwargs)
        else:
            raise LLMError(f"the API rejected the request: {exc}") from exc
    except anthropic.AuthenticationError as exc:
        raise LLMError(f"authentication failed — check ANTHROPIC_API_KEY: {exc}") from exc
    except anthropic.APIStatusError as exc:
        raise LLMError(f"API error {exc.status_code}: {exc}") from exc
    except anthropic.APIConnectionError as exc:
        raise LLMError(f"could not reach the API: {exc}") from exc

    usage = getattr(response, "usage", None)
    if budget is not None and usage is not None:
        budget.record(model, usage)
        trace(
            "llm_usage",
            {
                "model": model,
                "input_tokens": getattr(usage, "input_tokens", None),
                "output_tokens": getattr(usage, "output_tokens", None),
                "cache_read": getattr(usage, "cache_read_input_tokens", None),
                "cumulative": budget.total,
                "budget": budget.limit,
            },
        )
    return response


def _create(api: Any, kwargs: dict[str, Any]) -> Any:
    """Issue the request, preferring the refusal-fallback beta when available."""
    if _fallback_enabled and hasattr(api, "beta"):
        return api.beta.messages.create(
            **kwargs, betas=[_FALLBACK_BETA], fallbacks="default"
        )
    return api.messages.create(**kwargs)


def _looks_like_fallback_rejection(exc: Exception) -> bool:
    blob = str(exc).lower()
    return "fallback" in blob or "beta" in blob


def text_of(content: Iterable[Any]) -> str:
    """Concatenate the text blocks of a response, ignoring thinking and tool_use."""
    return "".join(
        block.text
        for block in content
        if getattr(block, "type", None) == "text" and getattr(block, "text", None)
    ).strip()
