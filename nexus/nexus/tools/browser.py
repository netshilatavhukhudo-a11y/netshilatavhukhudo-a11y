"""Drive a real browser with Playwright (headless Chromium).

Actions: goto, click, fill, extract_text, screenshot. Use when a task needs
interaction — logins, forms, buttons, JS-heavy sites — rather than the static
read that web_fetch gives.

Phase 3 opens one browser session per call: simple and stateless. Phase 4
swaps `_run_action` for a persistent context so a login -> navigate -> submit
flow can share cookies across calls, without touching this file's public
surface.

The tier split lives in permissions.py (`browser.read` = auto,
`browser.submit_form` = confirm), keyed off `action`, so clicking a button
requires approval while reading a page does not.
"""

from __future__ import annotations

import os
from pathlib import Path

from nexus import workspace_dir
from nexus.tools.base import ToolError, tool
from nexus.tools.web_fetch import guard_url
from nexus.tracing import trace

_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["goto", "click", "fill", "extract_text", "screenshot"],
        },
        "url": {"type": "string"},
        "selector": {
            "type": "string",
            "description": "CSS selector for click/fill/extract.",
        },
        "value": {"type": "string", "description": "Text to type for 'fill'."},
    },
    "required": ["action"],
}

_MAX_TEXT_CHARS = 8000
_NAV_TIMEOUT_MS = 30000
_ACTION_TIMEOUT_MS = 15000

# Reused across calls once a session has navigated somewhere. Phase 3 keeps
# state only within a single tool call; this holds the current page URL so an
# action that omits `url` operates on the last page the agent loaded.
_last_url: str | None = None


def _import_playwright():
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - registry reports this
        raise ToolError(
            "Playwright is not installed. Run `pip install playwright` and "
            "`python -m playwright install chromium`."
        ) from exc
    return sync_playwright


def _discover_chromium() -> str | None:
    """Find a Chromium binary when the pip version and the installed browser
    build don't match.

    Explicit override first (`NEXUS_CHROMIUM_PATH`), then the newest build
    under `PLAYWRIGHT_BROWSERS_PATH`. Returns None to let Playwright use its
    own managed download (the normal case on a developer machine that ran
    `playwright install`).
    """
    override = os.getenv("NEXUS_CHROMIUM_PATH")
    if override and Path(override).exists():
        return override

    root = os.getenv("PLAYWRIGHT_BROWSERS_PATH")
    if not root or not Path(root).is_dir():
        return None

    candidates: list[Path] = []
    for pattern in ("chromium-*/chrome-linux/chrome", "chromium_headless_shell-*/chrome-linux/headless_shell"):
        candidates.extend(Path(root).glob(pattern))
    if not candidates:
        return None
    # Highest build number wins (chromium-1194 vs chromium-1180).
    best = max(candidates, key=lambda p: int("".join(c for c in p.parts[-3] if c.isdigit()) or 0))
    return str(best)


def _launch(pw):
    """Launch headless Chromium, falling back to a discovered binary on skew."""
    exe = _discover_chromium()
    kwargs = {"headless": True}
    if exe:
        kwargs["executable_path"] = exe
        trace("thought", {"text": f"browser: using discovered Chromium at {exe}"})
    try:
        return pw.chromium.launch(**kwargs)
    except Exception as exc:  # noqa: BLE001 - surfaced as a tool error
        raise ToolError(
            f"could not launch Chromium: {str(exc).splitlines()[0]}. "
            f"Set NEXUS_CHROMIUM_PATH to a chrome binary, or run "
            f"`python -m playwright install chromium`."
        ) from exc


def _run_action(inp: dict) -> str:
    """Open a browser, perform one action, tear the browser down."""
    global _last_url
    sync_playwright = _import_playwright()
    action = inp["action"]

    # Every action except an explicit goto operates on a URL; default to the
    # last one the agent visited so multi-step flows read naturally.
    url = (inp.get("url") or "").strip() or _last_url
    if action == "goto" and not (inp.get("url") or "").strip():
        raise ToolError("action='goto' requires a 'url'")
    if url:
        guard_url(url)

    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page()
        page.set_default_timeout(_ACTION_TIMEOUT_MS)
        page.set_default_navigation_timeout(_NAV_TIMEOUT_MS)

        try:
            if url:
                page.goto(url, wait_until="domcontentloaded")
                _last_url = url

            if action == "goto":
                return f"Loaded {url} — title: {page.title()!r}"

            if action == "extract_text":
                selector = inp.get("selector") or "body"
                try:
                    text = page.inner_text(selector)
                except Exception as exc:  # noqa: BLE001
                    raise ToolError(f"could not extract {selector!r}: {exc}") from exc
                text = "\n".join(line for line in (ln.strip() for ln in text.splitlines()) if line)
                if len(text) > _MAX_TEXT_CHARS:
                    text = text[:_MAX_TEXT_CHARS] + f"\n[truncated at {_MAX_TEXT_CHARS} chars]"
                return f"Text from {selector!r} on {page.url}:\n\n{text or '[no text]'}"

            if action == "fill":
                selector, value = inp.get("selector"), inp.get("value")
                if not selector:
                    raise ToolError("action='fill' requires a 'selector'")
                if value is None:
                    raise ToolError("action='fill' requires a 'value'")
                page.fill(selector, str(value))
                return f"Filled {selector!r} with {len(str(value))} chars on {page.url}"

            if action == "click":
                selector = inp.get("selector")
                if not selector:
                    raise ToolError("action='click' requires a 'selector'")
                page.click(selector)
                page.wait_for_load_state("domcontentloaded")
                _last_url = page.url
                return f"Clicked {selector!r}; now at {page.url} — title: {page.title()!r}"

            if action == "screenshot":
                shots = workspace_dir() / "screenshots"
                shots.mkdir(parents=True, exist_ok=True)
                path = shots / f"shot-{abs(hash(page.url)) % 10**8:08d}.png"
                page.screenshot(path=str(path), full_page=True)
                return f"Saved screenshot of {page.url} to {path.relative_to(workspace_dir())}"

            raise ToolError(f"unknown browser action: {action!r}")
        finally:
            browser.close()


@tool(
    "browser",
    "Drive a real browser. Actions: goto, click, fill, extract_text, "
    "screenshot. Use when a task needs interaction (logins, forms, buttons, "
    "JS-heavy sites) rather than static reading.",
    _SCHEMA,
    tier_key="browser",
)
def browser(inp: dict) -> str:
    action = (inp.get("action") or "").strip().lower()
    if action not in {"goto", "click", "fill", "extract_text", "screenshot"}:
        raise ToolError(
            f"unknown action {action!r}; expected goto, click, fill, "
            f"extract_text, or screenshot"
        )
    trace("thought", {"text": f"browser {action} {inp.get('url') or inp.get('selector') or ''}"})
    inp = {**inp, "action": action}

    from playwright.sync_api import Error as PlaywrightError  # noqa: PLC0415

    try:
        return _run_action(inp)
    except PlaywrightError as exc:
        # Timeouts, missing selectors, navigation failures — all recoverable;
        # hand the model a clean message so it can re-plan.
        raise ToolError(f"browser {action} failed: {str(exc).splitlines()[0]}") from exc
