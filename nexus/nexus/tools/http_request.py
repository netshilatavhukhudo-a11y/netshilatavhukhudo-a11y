"""Call any HTTP API. Returns status and body.

Tiering is by method (`http_request.GET` = auto, `http_request.POST` =
confirm), enforced in permissions.py — reads are free, writes need approval.
Every request URL, including redirect hops, passes guard_url, so this cannot
become a way to reach the cloud metadata endpoint or the host network.
"""

from __future__ import annotations

import json as jsonlib

import httpx

from nexus.tools.base import ToolError, tool
from nexus.tools.web_fetch import build_client, guard_url

_SCHEMA = {
    "type": "object",
    "properties": {
        "method": {"type": "string", "enum": ["GET", "POST", "PUT", "DELETE"]},
        "url": {"type": "string"},
        "headers": {"type": "object"},
        "json_body": {"type": "object"},
    },
    "required": ["method", "url"],
}

_MAX_BODY_CHARS = 8000
_MAX_REDIRECTS = 5


@tool(
    "http_request",
    "Call any HTTP API. Returns status and body. Use for programmatic "
    "integrations. Methods: GET, POST, PUT, DELETE.",
    _SCHEMA,
    tier_key="http_request",
)
def http_request(inp: dict) -> str:
    method = (inp.get("method") or "").strip().upper()
    if method not in {"GET", "POST", "PUT", "DELETE"}:
        raise ToolError(f"unsupported method {method!r}; expected GET, POST, PUT, or DELETE")

    url = (inp.get("url") or "").strip()
    if not url:
        raise ToolError("http_request requires a 'url'")

    headers = inp.get("headers") or {}
    if not isinstance(headers, dict):
        raise ToolError("'headers' must be an object")

    json_body = inp.get("json_body")
    if json_body is not None and not isinstance(json_body, dict):
        raise ToolError("'json_body' must be an object")

    current = url
    with build_client() as client:
        try:
            # Follow redirects by hand so each hop is guarded, and so a POST
            # isn't silently replayed against a redirect target the model
            # never saw.
            for _ in range(_MAX_REDIRECTS + 1):
                guard_url(current)
                response = client.request(
                    method,
                    current,
                    headers={str(k): str(v) for k, v in headers.items()},
                    json=json_body if method in {"POST", "PUT"} else None,
                )
                if not response.is_redirect:
                    break
                location = response.headers.get("location")
                if not location:
                    break
                current = str(response.url.join(location))
            else:
                raise ToolError(f"too many redirects ({_MAX_REDIRECTS}) starting at {url}")
        except httpx.HTTPError as exc:
            raise ToolError(f"{method} {url} failed: {exc}") from exc

    content_type = response.headers.get("content-type", "")
    if "json" in content_type:
        try:
            body = jsonlib.dumps(response.json(), indent=2)[:_MAX_BODY_CHARS]
        except ValueError:
            body = response.text[:_MAX_BODY_CHARS]
    else:
        body = response.text[:_MAX_BODY_CHARS]

    truncated = "\n[body truncated]" if len(response.text) > _MAX_BODY_CHARS else ""
    return f"HTTP {response.status_code} {response.reason_phrase} ({content_type})\n\n{body}{truncated}"
