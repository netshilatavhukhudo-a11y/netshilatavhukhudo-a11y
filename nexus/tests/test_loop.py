"""The agent loop end to end, driven by a scripted brain.

Covers: a multi-step goal that calls a tool and finishes; the assistant turn
(and thinking) being threaded back into messages; multiple tool_use blocks in
one turn; the request shape llm.py builds; stop conditions (max_steps, refusal,
token budget); and the permission gate blocking a call inside a real run.
"""

from __future__ import annotations

import json

from conftest import Response, Text, Thinking, ToolUse
from nexus.orchestrator import run
from nexus.tracing import Tracer, use_tracer


def _events(tracer):
    return [json.loads(line) for line in tracer.path.read_text().splitlines()]


def test_two_step_goal_calls_tool_then_finishes(scripted, config_with):
    cfg = config_with(["calculator"])

    turns = [
        lambda m: Response(
            [Text("computing"), ToolUse("calculator", {"expression": "6 * 7"}, "t1")],
            "tool_use",
        ),
        lambda m: Response([Text(f"The answer is {m[-1]['content'][0]['content']}.")], "end_turn"),
    ]
    scripted(turns)

    with use_tracer(Tracer()) as tracer:
        result = run("what is six times seven", cfg)

    assert result.ok
    assert result.status == "done"
    assert result.steps == 2
    assert "42" in result.answer
    # budget accounting ran inside the real call_llm
    assert result.usage["calls"] == 2
    assert result.usage["total"] == 2 * (100 + 20)

    kinds = [e["event"] for e in _events(tracer)]
    assert kinds[0] == "run_start" and kinds[-1] == "run_end"
    assert "tool_call" in kinds and "tool_result" in kinds and "final" in kinds


def test_conversation_is_threaded_not_rebuilt(scripted, config_with):
    cfg = config_with(["calculator"])
    scripted(
        [
            lambda m: Response([ToolUse("calculator", {"expression": "1+1"}, "t1")], "tool_use"),
            lambda m: Response([Text("done")], "end_turn"),
        ]
    )
    client = None
    import nexus.llm as llm

    with use_tracer(Tracer()):
        run("add", cfg)
    client = llm.get_client()

    # Second request carries: user goal, assistant tool_use, user tool_result.
    roles = [m["role"] for m in client.sent[-1]["messages"]]
    assert roles == ["user", "assistant", "user"]
    tool_results = client.sent[-1]["messages"][-1]["content"]
    assert tool_results[0]["tool_use_id"] == "t1"


def test_llm_request_shape_matches_current_api(scripted, config_with):
    cfg = config_with(["calculator"])
    scripted([lambda m: Response([Text("hi")], "end_turn")])

    with use_tracer(Tracer()):
        run("hello", cfg)

    import nexus.llm as llm

    sent = llm.get_client().sent[0]
    assert sent["model"] == "claude-opus-5"
    assert sent["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert sent["output_config"] == {"effort": "high"}
    assert sent["cache_control"] == {"type": "ephemeral"}
    # sampling params are removed on this model and would 400
    assert not ({"temperature", "top_p", "top_k"} & set(sent))
    assert [t["name"] for t in sent["tools"]] == ["calculator"]


def test_multiple_tool_uses_in_one_turn(scripted, config_with):
    cfg = config_with(["calculator"])
    scripted(
        [
            lambda m: Response(
                [
                    ToolUse("calculator", {"expression": "2+2"}, "a"),
                    ToolUse("calculator", {"expression": "10*10"}, "b"),
                ],
                "tool_use",
            ),
            lambda m: Response([Text("both done")], "end_turn"),
        ]
    )
    with use_tracer(Tracer()) as tracer:
        result = run("two sums", cfg)

    assert result.ok
    results = [e for e in _events(tracer) if e["event"] == "tool_result"]
    assert [r["data"]["id"] for r in results] == ["a", "b"]
    assert results[0]["data"]["result"] == "4"
    assert results[1]["data"]["result"] == "100"


def test_thinking_blocks_are_traced_and_preserved(scripted, config_with):
    cfg = config_with(["calculator"])
    scripted([lambda m: Response([Thinking("let me reason"), Text("answer")], "end_turn")])

    with use_tracer(Tracer()) as tracer:
        run("think", cfg)

    thoughts = [e for e in _events(tracer) if e["event"] == "thought"]
    assert any("let me reason" in t["data"].get("text", "") for t in thoughts)


def test_max_steps_stops_the_loop(scripted, config_with):
    cfg = config_with(["calculator"])
    cfg["limits"]["max_steps"] = 3
    # never stops asking for a tool -> loop must hit the cap
    scripted([lambda m: Response([ToolUse("calculator", {"expression": "1+1"}, "x")], "tool_use")] * 3)

    with use_tracer(Tracer()):
        result = run("loop forever", cfg)

    assert result.status == "max_steps"
    assert result.steps == 3
    assert not result.ok


def test_refusal_is_surfaced(scripted, config_with):
    cfg = config_with(["calculator"])

    class Details:
        category = "cyber"

    scripted([lambda m: Response([], "refusal", stop_details=Details())])

    with use_tracer(Tracer()):
        result = run("something disallowed", cfg)

    assert result.status == "refused"
    assert "cyber" in result.answer


def test_token_budget_stops_the_run(scripted, config_with):
    cfg = config_with(["calculator"])
    # The budget is checked at the START of each call, before that call's
    # tokens are recorded. Turn 1 (total 0 < 100) proceeds and records 120;
    # turn 2's pre-call check (120 >= 100) then raises.
    cfg["limits"]["max_tokens_budget"] = 100
    scripted(
        [
            lambda m: Response([ToolUse("calculator", {"expression": "1+1"}, "x")], "tool_use"),
            lambda m: Response([Text("never reached")], "end_turn"),
        ]
    )
    with use_tracer(Tracer()):
        result = run("burn budget", cfg)

    assert result.status == "budget"
    assert "budget" in result.answer.lower()


def test_permission_gate_blocks_inside_a_run(scripted, config_with, monkeypatch):
    # code_exec is confirm-tier; with no terminal the gate denies, and the
    # model gets a [BLOCKED] result rather than the tool running.
    monkeypatch.setenv("NEXUS_NONINTERACTIVE", "1")
    cfg = config_with(["code_exec"])
    scripted(
        [
            lambda m: Response([ToolUse("code_exec", {"code": "print('should not run')"}, "c1")], "tool_use"),
            lambda m: Response([Text(f"result was: {m[-1]['content'][0]['content'][:40]}")], "end_turn"),
        ]
    )
    with use_tracer(Tracer()) as tracer:
        result = run("run code", cfg)

    assert result.ok  # the run completes; the tool just didn't execute
    blocked = [e for e in _events(tracer) if e["event"] == "blocked"]
    assert blocked, "expected a blocked event"
    tool_results = [e for e in _events(tracer) if e["event"] == "tool_result"]
    assert "[BLOCKED]" in tool_results[0]["data"]["result"]
