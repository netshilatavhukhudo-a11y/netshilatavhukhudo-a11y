"""Shared fixtures.

Every test runs against a fresh temp workspace and temp trace dir, so the
suite never touches the real `workspace/` or `traces/`. No test needs the
network or an API key — the LLM is faked at the transport boundary.
"""

from __future__ import annotations

import http.server
import json
import socketserver
import threading
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest


@pytest.fixture(autouse=True)
def sandboxed_env(tmp_path, monkeypatch):
    """Point the whole agent at throwaway directories for the test."""
    ws = tmp_path / "workspace"
    traces = tmp_path / "traces"
    ws.mkdir()
    traces.mkdir()
    monkeypatch.setenv("NEXUS_WORKSPACE", str(ws))
    monkeypatch.setenv("NEXUS_TRACE_DIR", str(traces))
    # Reset per-tool module singletons that cache the old workspace path.
    import nexus.tools.memory_tool as mt

    mt._store = None
    yield ws


@pytest.fixture
def config():
    from nexus import load_config

    root = Path(__file__).resolve().parent.parent
    return load_config(root / "config" / "agent.json")


@pytest.fixture
def config_with(config):
    """Return a copy of the config with a chosen enabled_tools list."""

    def make(tools):
        c = json.loads(json.dumps(config))
        c["enabled_tools"] = tools
        return c

    return make


# --------------------------------------------------------------------------
# Fake LLM: a scripted brain patched in at the transport boundary.
# --------------------------------------------------------------------------
@dataclass
class Text:
    text: str
    type: str = "text"


@dataclass
class ToolUse:
    name: str
    input: dict
    id: str
    type: str = "tool_use"


@dataclass
class Thinking:
    thinking: str
    type: str = "thinking"


@dataclass
class Usage:
    input_tokens: int = 100
    output_tokens: int = 20
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class Response:
    content: list
    stop_reason: str
    usage: Usage = field(default_factory=Usage)
    stop_details: object = None


class ScriptedClient:
    """Plays back a list of turn-builders. Each is called with the current
    messages list and returns a Response, so scripts can react to tool output.
    """

    def __init__(self, turns):
        self._turns = list(turns)
        self.sent = []

        client = self

        class _Messages:
            def create(self, **kwargs):
                client.sent.append({**kwargs, "messages": list(kwargs["messages"])})
                builder = client._turns[len(client.sent) - 1]
                return builder(kwargs["messages"])

        self.messages = _Messages()

        class _Beta:
            messages = self.messages

        self.beta = _Beta()


@pytest.fixture
def scripted(monkeypatch):
    """Install a ScriptedClient as the LLM. Pass a list of turn-builders."""

    def install(turns):
        import nexus.llm as llm

        client = ScriptedClient(turns)
        monkeypatch.setattr(llm, "get_client", lambda *a, **k: client)
        monkeypatch.setattr(llm, "_client", client, raising=False)
        return client

    return install


# --------------------------------------------------------------------------
# A tiny local HTTP server, so http_request / browser tests need no network.
# --------------------------------------------------------------------------
class _Handler(http.server.BaseHTTPRequestHandler):
    received: dict = {}

    def _reply(self, body: bytes, ctype="application/json"):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        path = urlparse(self.path).path
        if path == "/form":
            self._reply(
                b"<!doctype html><title>Form</title>"
                b"<form action='/done' method='get'>"
                b"<input id='q' name='q'/><button id='go' type='submit'>Go</button></form>",
                "text/html",
            )
        elif path == "/done":
            q = parse_qs(urlparse(self.path).query).get("q", [""])[0]
            self._reply(f"<!doctype html><title>Done</title><h1 id='r'>Got {q}</h1>".encode(), "text/html")
        elif path == "/page":
            self._reply(
                b"<!doctype html><html><head><title>Doc</title></head>"
                b"<body><nav>menu</nav><h1>Heading</h1><p>Para one.</p>"
                b"<script>var x=1;</script><p>Para two.</p></body></html>",
                "text/html",
            )
        else:
            self._reply(b'{"ok": true, "method": "GET"}')

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length", 0))
        _Handler.received["body"] = self.rfile.read(n).decode()
        self._reply(b'{"ok": true, "method": "POST"}')

    def log_message(self, *_a):
        pass


@pytest.fixture
def http_server():
    _Handler.received = {}
    with socketserver.TCPServer(("127.0.0.1", 0), _Handler) as httpd:
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{httpd.server_address[1]}", _Handler
        finally:
            httpd.shutdown()


@pytest.fixture
def allow_private(monkeypatch):
    """Let guard_url reach the loopback test server."""
    monkeypatch.setenv("NEXUS_ALLOW_PRIVATE_NETWORK", "1")
