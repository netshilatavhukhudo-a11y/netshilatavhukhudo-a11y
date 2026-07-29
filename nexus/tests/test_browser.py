"""Browser tool against a local server. Skipped if Playwright/Chromium absent."""

from __future__ import annotations

import pytest

from nexus.registry import load_tools
from nexus.tools.base import REGISTRY, ToolError

playwright = pytest.importorskip("playwright.sync_api")


def _goto_or_skip(handler, url):
    """Perform the first goto; skip the test if Chromium simply cannot launch."""
    try:
        return handler({"action": "goto", "url": url})
    except ToolError as exc:
        if "could not launch" in str(exc).lower():
            pytest.skip("Chromium not launchable in this environment")
        raise


@pytest.fixture
def browser_tool(sandboxed_env):
    load_tools(["browser"])
    if "browser" not in REGISTRY:
        pytest.skip("browser tool did not register")
    return REGISTRY["browser"].handler


def test_full_flow_persists_state(browser_tool, http_server, allow_private):
    base, _ = http_server

    assert "Loaded" in _goto_or_skip(browser_tool, f"{base}/form")
    # fill in one call, submit in a SEPARATE call — value must persist
    assert "Filled" in browser_tool({"action": "fill", "selector": "#q", "value": "hello"})
    result = browser_tool({"action": "click", "selector": "#go"})
    assert "q=hello" in result
    assert "Got hello" in browser_tool({"action": "extract_text", "selector": "#r"})


def test_screenshot_writes_png(browser_tool, http_server, allow_private, sandboxed_env):
    base, _ = http_server
    _goto_or_skip(browser_tool, f"{base}/form")
    out = browser_tool({"action": "screenshot", "url": f"{base}/form"})
    assert ".png" in out
    pngs = list((sandboxed_env / "screenshots").glob("*.png"))
    assert pngs and pngs[0].read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_private_address_guarded_by_default(browser_tool, http_server):
    base, _ = http_server
    with pytest.raises(ToolError, match="non-public"):
        browser_tool({"action": "goto", "url": f"{base}/form"})


def test_bad_action_rejected(browser_tool):
    with pytest.raises(ToolError, match="unknown action"):
        browser_tool({"action": "teleport"})
