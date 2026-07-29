"""The extension mechanism: adding a tool touches only tools/ + enabled_tools.

This is the Definition-of-Done claim, tested directly. The 8th tool
(calculator) was added as one file plus one config entry and one tier — no
change to orchestrator, registry, llm, permissions, or tracing.
"""

from __future__ import annotations

import pytest

from nexus.registry import (
    ToolLoadError,
    export_schemas,
    get_tool,
    load_tools,
)


def test_load_all_configured_tools(config):
    loaded, skipped = load_tools(config["enabled_tools"])
    # every configured tool loads, or is skipped only for a missing dependency
    assert set(loaded) | set(skipped) == set(config["enabled_tools"])
    for reason in skipped.values():
        assert "missing dependency" in reason


def test_schemas_are_api_shaped(config):
    load_tools(config["enabled_tools"])
    for schema in export_schemas(config["enabled_tools"]):
        assert set(schema) >= {"name", "description", "input_schema"}
        assert schema["input_schema"]["type"] == "object"


def test_calculator_is_the_8th_tool_and_self_contained(config):
    # It is registered purely by importing its module — the @tool decorator.
    load_tools(["calculator"])
    tool = get_tool("calculator")
    assert tool.name == "calculator"
    assert tool.tier_key == "calculator"
    # and it works with no wiring elsewhere
    assert tool.handler({"expression": "9 * 9"}) == "81"


def test_unknown_enabled_tool_is_a_clear_error():
    with pytest.raises(ToolLoadError, match="does not exist"):
        load_tools(["this_tool_is_not_real"])


def test_missing_optional_dependency_is_skipped_not_fatal(monkeypatch):
    # A tool module that fails to import because a THIRD-PARTY package is
    # absent is skipped with a reason; the rest of the agent is unaffected.
    import importlib

    real_import = importlib.import_module

    def fake_import(name, *args, **kwargs):
        if name == "nexus.tools.browser":
            raise ModuleNotFoundError("No module named 'playwright'", name="playwright")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", fake_import)

    loaded, skipped = load_tools(["calculator", "browser"])
    assert loaded == ["calculator"]
    assert "browser" in skipped
    assert "missing dependency 'playwright'" in skipped["browser"]


def test_missing_tool_module_still_raises(monkeypatch):
    # But a MISSING tool file (not a third-party dep) is a config error and
    # must surface, not be silently skipped.
    with pytest.raises(ToolLoadError, match="does not exist"):
        load_tools(["totally_made_up_tool"])
