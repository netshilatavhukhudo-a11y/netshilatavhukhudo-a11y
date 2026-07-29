"""Permission tiers and the human approval gate."""

from __future__ import annotations

import builtins

import pytest

from nexus.permissions import authorize, resolve_tier


class _Tty:
    def isatty(self):
        return True


@pytest.fixture
def answer(monkeypatch):
    """Feed a fixed answer to the approval prompt, pretending to be a tty."""

    def feed(value):
        monkeypatch.setattr("sys.stdin", _Tty())
        monkeypatch.setattr(builtins, "input", lambda *a, **k: value)

    return feed


# -- tier resolution comes from config, keyed off the tool input -----------
@pytest.mark.parametrize(
    "tool,inp,tier",
    [
        ("web_search", {}, "auto"),
        ("web_fetch", {}, "auto"),
        ("file_ops", {"op": "read"}, "auto"),
        ("file_ops", {"op": "write"}, "confirm"),
        ("file_ops", {"op": "delete"}, "block"),
        ("http_request", {"method": "GET"}, "auto"),
        ("http_request", {"method": "POST"}, "confirm"),
        ("code_exec", {}, "confirm"),
        ("browser", {"action": "goto"}, "auto"),
        ("browser", {"action": "extract_text"}, "auto"),
        ("browser", {"action": "click"}, "confirm"),
        ("browser", {"action": "fill"}, "confirm"),
        ("payments.transfer", {}, "block"),
        ("live_pricing.set", {}, "block"),
    ],
)
def test_resolve_tier(config, tool, inp, tier):
    assert resolve_tier(tool, inp, config).tier == tier


# -- the model cannot reach a weaker tier by reshaping input ---------------
@pytest.mark.parametrize(
    "tool,inp",
    [
        ("file_ops", {}),  # no op at all
        ("file_ops", {"op": "DELETE"}),  # case
        ("file_ops", {"op": "wat"}),  # unknown op
    ],
)
def test_missing_or_bogus_discriminator_falls_back_to_strictest(config, tool, inp):
    # file_ops has a block-tier member (delete), so an unreadable op must not
    # slip through at a weaker tier.
    assert resolve_tier(tool, inp, config).tier == "block"


def test_http_request_no_method_uses_strictest_configured(config):
    # http_request.* is at most confirm, so the fallback is confirm not block.
    assert resolve_tier("http_request", {}, config).tier == "confirm"


def test_unknown_tool_uses_default_tier(config):
    assert resolve_tier("brand_new_tool", {}, config).tier == config["permissions"]["default_tier"]


# -- the gate ---------------------------------------------------------------
def test_auto_never_prompts(config):
    allowed, decision = authorize("web_search", {"query": "x"}, config)
    assert allowed and decision.tier == "auto"


def test_confirm_accepts_yes(config, answer):
    answer("y")
    assert authorize("file_ops", {"op": "write", "path": "a"}, config)[0]
    answer("yes")
    assert authorize("file_ops", {"op": "write", "path": "a"}, config)[0]


def test_confirm_rejects_anything_else(config, answer):
    for value in ("n", "no", "", "maybe", "nope"):
        answer(value)
        assert not authorize("file_ops", {"op": "write", "path": "a"}, config)[0], value


def test_confirm_tolerates_case_and_surrounding_space(config, answer):
    # confirm is a low bar (reversible action): 'y'/'yes' with padding or case
    # is a yes. Only the block phrase is exact.
    for value in ("Y", "Yes", " y ", "YES"):
        answer(value)
        assert authorize("file_ops", {"op": "write", "path": "a"}, config)[0], value


def test_block_needs_exact_typed_phrase(config, answer):
    for value in ("y", "yes", "approve", " APPROVE ", "APPROVE!"):
        answer(value)
        assert not authorize("file_ops", {"op": "delete", "path": "a"}, config)[0], value
    answer("APPROVE")
    assert authorize("file_ops", {"op": "delete", "path": "a"}, config)[0]


def test_block_asks_again_every_time(config, answer):
    answer("APPROVE")
    assert authorize("file_ops", {"op": "delete", "path": "a"}, config)[0]
    # a subsequent call is a fresh prompt — no "approve all" memory
    answer("y")
    assert not authorize("file_ops", {"op": "delete", "path": "a"}, config)[0]


def test_fails_closed_without_a_terminal(config, monkeypatch):
    monkeypatch.setenv("NEXUS_NONINTERACTIVE", "1")
    assert not authorize("file_ops", {"op": "write", "path": "a"}, config)[0]
    assert not authorize("file_ops", {"op": "delete", "path": "a"}, config)[0]


def test_non_interactive_never_auto_approves(config, monkeypatch):
    # There must be no way to make the non-interactive path say yes.
    monkeypatch.setenv("NEXUS_NONINTERACTIVE", "1")
    monkeypatch.setattr(builtins, "input", lambda *a, **k: "APPROVE")  # even if input existed
    assert not authorize("file_ops", {"op": "delete", "path": "a"}, config)[0]
