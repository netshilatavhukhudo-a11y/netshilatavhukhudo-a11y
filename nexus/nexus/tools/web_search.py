"""Search the web and return the top results.

Provider is picked by which key is present, so the tool works with no
configuration at all (DuckDuckGo) and gets better when a key is supplied:

    BRAVE_API_KEY  -> Brave Search API
    SERPER_API_KEY -> Serper (Google)
    (neither)      -> DuckDuckGo HTML endpoint, keyless but rate-limited

Adding a provider means adding one function and one entry in `_PROVIDERS`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from nexus.tools.base import ToolError, tool
from nexus.tools.web_fetch import build_client
from nexus.tracing import trace

_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Search query, 1-8 words."},
        "num_results": {"type": "integer", "default": 5},
    },
    "required": ["query"],
}

_MAX_RESULTS = 10
_SNIPPET_CHARS = 300


@dataclass
class Result:
    title: str
    url: str
    snippet: str


# --------------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------------
def _brave(query: str, n: int, key: str) -> list[Result]:
    with build_client(follow_redirects=True) as client:
        response = client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": n},
            headers={"Accept": "application/json", "X-Subscription-Token": key},
        )
    if response.status_code != 200:
        raise ToolError(f"Brave search failed with HTTP {response.status_code}: {response.text[:200]}")
    return [
        Result(item.get("title", ""), item.get("url", ""), item.get("description", ""))
        for item in response.json().get("web", {}).get("results", [])[:n]
    ]


def _serper(query: str, n: int, key: str) -> list[Result]:
    with build_client(follow_redirects=True) as client:
        response = client.post(
            "https://google.serper.dev/search",
            json={"q": query, "num": n},
            headers={"X-API-KEY": key, "Content-Type": "application/json"},
        )
    if response.status_code != 200:
        raise ToolError(f"Serper search failed with HTTP {response.status_code}: {response.text[:200]}")
    return [
        Result(item.get("title", ""), item.get("link", ""), item.get("snippet", ""))
        for item in response.json().get("organic", [])[:n]
    ]


class _DDGParser(HTMLParser):
    """Pull `(title, url, snippet)` triples out of the DuckDuckGo HTML page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[Result] = []
        self._href = ""
        self._title: list[str] = []
        self._snippet: list[str] = []
        self._mode = ""  # "" | "title" | "snippet"

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if "result__a" in classes:
            self._flush()
            self._href = _unwrap_ddg(attributes.get("href") or "")
            self._mode = "title"
        elif "result__snippet" in classes:
            self._mode = "snippet"

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._mode = ""

    def handle_data(self, data: str) -> None:
        if self._mode == "title":
            self._title.append(data)
        elif self._mode == "snippet":
            self._snippet.append(data)

    def _flush(self) -> None:
        if self._href and self._title:
            self.results.append(
                Result(
                    " ".join("".join(self._title).split()),
                    self._href,
                    " ".join("".join(self._snippet).split()),
                )
            )
        self._href, self._title, self._snippet = "", [], []

    def close(self) -> None:
        super().close()
        self._flush()


def _unwrap_ddg(href: str) -> str:
    """DuckDuckGo wraps results as //duckduckgo.com/l/?uddg=<encoded>."""
    if "uddg=" not in href:
        return href if href.startswith("http") else f"https:{href}" if href.startswith("//") else href
    target = parse_qs(urlparse(href if href.startswith("http") else f"https:{href}").query).get("uddg")
    return unquote(target[0]) if target else href


def _duckduckgo(query: str, n: int, _key: str = "") -> list[Result]:
    with build_client(follow_redirects=True) as client:
        response = client.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if response.status_code != 200:
        raise ToolError(
            f"DuckDuckGo search failed with HTTP {response.status_code}. "
            f"The keyless endpoint rate-limits aggressively — set BRAVE_API_KEY "
            f"or SERPER_API_KEY in .env for a reliable provider."
        )
    parser = _DDGParser()
    parser.feed(response.text)
    parser.close()
    return parser.results[:n]


# (env var, label, function). First entry whose key is set wins.
_PROVIDERS = (
    ("BRAVE_API_KEY", "brave", _brave),
    ("SERPER_API_KEY", "serper", _serper),
    ("", "duckduckgo", _duckduckgo),
)


def _select_provider() -> tuple[str, object, str]:
    for env_var, label, fn in _PROVIDERS:
        key = os.getenv(env_var, "").strip() if env_var else ""
        if key or not env_var:
            return label, fn, key
    raise ToolError("no search provider available")  # unreachable: DDG has no key


@tool(
    "web_search",
    "Search the web and return the top results with titles, URLs, and "
    "snippets. Use for finding current information or source pages.",
    _SCHEMA,
    tier_key="web_search",
)
def web_search(inp: dict) -> str:
    query = (inp.get("query") or "").strip()
    if not query:
        raise ToolError("web_search requires a non-empty 'query'")

    try:
        n = max(1, min(_MAX_RESULTS, int(inp.get("num_results") or 5)))
    except (TypeError, ValueError):
        n = 5

    label, fn, key = _select_provider()
    trace("thought", {"text": f"web_search via {label}: {query!r}"})

    try:
        results = fn(query, n, key)  # type: ignore[operator]
    except (httpx.ProxyError, httpx.ConnectError, httpx.ConnectTimeout) as exc:
        # Distinguish "the network will not let me out" from "the query was
        # bad" — they look identical to the model otherwise, and it will waste
        # steps rephrasing a query that was never the problem.
        raise ToolError(
            f"could not reach the {label} search endpoint ({exc}). This is an "
            f"egress/allowlist or DNS problem, not a bad query — rephrasing will "
            f"not help. Set BRAVE_API_KEY or SERPER_API_KEY in .env, or allow "
            f"the provider's host through the network policy."
        ) from exc
    except httpx.HTTPError as exc:
        raise ToolError(f"search request failed ({label}): {exc}") from exc

    if not results:
        return f"No results for {query!r} (provider: {label})."

    lines = [f"{len(results)} result(s) for {query!r} via {label}:"]
    for i, r in enumerate(results, 1):
        snippet = r.snippet[:_SNIPPET_CHARS] + ("…" if len(r.snippet) > _SNIPPET_CHARS else "")
        lines.append(f"\n{i}. {r.title}\n   URL: {r.url}\n   {snippet}")
    return "\n".join(lines)
