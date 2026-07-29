"""Fetch a URL and return cleaned, readable text."""

from __future__ import annotations

import ipaddress
import os
import socket
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

from nexus.tools.base import ToolError, tool

__all__ = ["build_client", "guard_url", "html_to_text"]

_SCHEMA = {
    "type": "object",
    "properties": {"url": {"type": "string"}},
    "required": ["url"],
}

_MAX_CHARS = 8000
_MAX_BYTES = 5_000_000
_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
_UA = "NEXUS-agent/1.0 (+autonomous research agent)"
_MAX_REDIRECTS = 5

# Tags whose contents are chrome, not content.
_DROP = frozenset(
    {
        "script", "style", "noscript", "svg", "head", "nav", "footer",
        "aside", "form", "iframe", "template", "button", "select", "canvas",
    }
)
# Tags that imply a line break when they open or close.
_BLOCK = frozenset(
    {
        "p", "div", "br", "li", "tr", "section", "article", "header", "main",
        "blockquote", "pre", "td", "th", "hr", "h1", "h2", "h3", "h4", "h5", "h6",
        "ul", "ol", "table", "figure", "figcaption",
    }
)


# --------------------------------------------------------------------------
# URL safety. Shared with http_request and browser.
# --------------------------------------------------------------------------
def guard_url(url: str) -> str:
    """Reject anything that isn't a public http(s) URL.

    Without this, "fetch a URL" is also "read the cloud metadata endpoint" and
    "port-scan the host network" — an autonomous loop should not be one
    hallucinated hostname away from either. Set NEXUS_ALLOW_PRIVATE_NETWORK=1
    to opt out when deliberately pointing the agent at an internal service.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ToolError(
            f"refusing non-http(s) URL {url!r} (scheme={parsed.scheme or 'none'!r})"
        )
    host = parsed.hostname
    if not host:
        raise ToolError(f"refusing URL with no host: {url!r}")

    if os.getenv("NEXUS_ALLOW_PRIVATE_NETWORK") == "1":
        return url

    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise ToolError(f"could not resolve host {host!r}: {exc}") from exc

    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
            or addr.is_unspecified
        ):
            raise ToolError(
                f"refusing to fetch {host!r}: resolves to non-public address {addr}. "
                f"Set NEXUS_ALLOW_PRIVATE_NETWORK=1 to allow internal targets."
            )
    return url


def build_client(**kwargs: object) -> httpx.Client:
    """An httpx client with our defaults. `trust_env` picks up proxy/CA vars."""
    params: dict[str, object] = {
        "timeout": _TIMEOUT,
        "follow_redirects": False,  # each hop is re-validated by hand
        "headers": {"User-Agent": _UA},
    }
    params.update(kwargs)
    return httpx.Client(**params)  # type: ignore[arg-type]


def get_with_redirects(client: httpx.Client, url: str) -> httpx.Response:
    """GET, following redirects only to URLs that also pass `guard_url`."""
    seen = []
    for _ in range(_MAX_REDIRECTS + 1):
        guard_url(url)
        seen.append(url)
        response = client.get(url)
        if not response.is_redirect:
            return response
        location = response.headers.get("location")
        if not location:
            return response
        url = str(response.url.join(location))
    raise ToolError(f"too many redirects ({_MAX_REDIRECTS}): {' -> '.join(seen)}")


# --------------------------------------------------------------------------
# HTML -> text
# --------------------------------------------------------------------------
class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._drop_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in _DROP:
            self._drop_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _DROP:
            self._drop_depth = max(0, self._drop_depth - 1)
        elif tag == "title":
            self._in_title = False
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif self._drop_depth == 0 and data.strip():
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    """Return `(title, text)` with chrome stripped and whitespace collapsed."""
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 - malformed HTML is normal; keep what we got
        pass

    lines: list[str] = []
    for chunk in "".join(parser.parts).split("\n"):
        collapsed = " ".join(chunk.split())
        if collapsed:
            lines.append(collapsed)
    return " ".join(parser.title.split()), "\n".join(lines)


# --------------------------------------------------------------------------
@tool(
    "web_fetch",
    "Fetch a URL and return cleaned readable text. Use to read a page found "
    "via web_search.",
    _SCHEMA,
    tier_key="web_fetch",
)
def web_fetch(inp: dict) -> str:
    url = (inp.get("url") or "").strip()
    if not url:
        raise ToolError("web_fetch requires a 'url'")

    with build_client() as client:
        try:
            response = get_with_redirects(client, url)
        except httpx.HTTPError as exc:
            raise ToolError(f"request to {url} failed: {exc}") from exc

    if response.status_code >= 400:
        raise ToolError(f"{url} returned HTTP {response.status_code}")

    content_type = response.headers.get("content-type", "")
    body = response.content[:_MAX_BYTES]

    if "html" in content_type or body[:200].lstrip().lower().startswith(b"<!doctype html"):
        title, text = html_to_text(body.decode(response.encoding or "utf-8", errors="replace"))
        header = f"# {title}\nURL: {response.url}\n\n" if title else f"URL: {response.url}\n\n"
    elif content_type.startswith(("text/", "application/json", "application/xml")):
        title, text = "", body.decode(response.encoding or "utf-8", errors="replace")
        header = f"URL: {response.url} ({content_type})\n\n"
    else:
        return (
            f"URL: {response.url}\nContent-Type: {content_type or 'unknown'}\n"
            f"Not text — {len(response.content)} bytes, not decoded. "
            f"Use http_request if you need the raw body."
        )

    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS] + f"\n\n[truncated at {_MAX_CHARS} chars]"
    return header + (text or "[page had no extractable text]")
