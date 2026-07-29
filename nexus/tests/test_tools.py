"""Each tool, individually — the Definition-of-Done requirement.

Network-dependent tools are exercised against the local `http_server` fixture
or via their pure functions, so the suite runs with no internet and no API key.
"""

from __future__ import annotations

import json

import pytest

from nexus.registry import load_tools
from nexus.tools.base import REGISTRY, ToolError


@pytest.fixture
def tools():
    load_tools(
        [
            "web_search", "web_fetch", "http_request", "file_ops",
            "code_exec", "memory_tool", "calculator",
        ]
    )
    return REGISTRY


# -- calculator (also the 8th-tool extension proof) ------------------------
class TestCalculator:
    def test_basic(self, tools):
        assert tools["calculator"].handler({"expression": "2 * (3 + 4)"}) == "14"

    def test_precedence_and_power(self, tools):
        assert tools["calculator"].handler({"expression": "2 + 3 ** 2"}) == "11"

    def test_rejects_non_arithmetic(self, tools):
        with pytest.raises(ToolError):
            tools["calculator"].handler({"expression": "__import__('os').system('echo hi')"})

    def test_rejects_names(self, tools):
        with pytest.raises(ToolError):
            tools["calculator"].handler({"expression": "x + 1"})

    def test_empty(self, tools):
        with pytest.raises(ToolError):
            tools["calculator"].handler({"expression": ""})


# -- file_ops ---------------------------------------------------------------
class TestFileOps:
    def test_write_read_roundtrip(self, tools):
        fo = tools["file_ops"].handler
        assert "Wrote" in fo({"op": "write", "path": "a/b.txt", "content": "hello"})
        assert fo({"op": "read", "path": "a/b.txt"}) == "hello"

    def test_list(self, tools):
        fo = tools["file_ops"].handler
        fo({"op": "write", "path": "docs/one.txt", "content": "1"})
        listing = fo({"op": "list", "path": "docs"})
        assert "one.txt" in listing

    def test_delete_file(self, tools):
        fo = tools["file_ops"].handler
        fo({"op": "write", "path": "gone.txt", "content": "x"})
        assert "Deleted" in fo({"op": "delete", "path": "gone.txt"})
        with pytest.raises(ToolError):
            fo({"op": "read", "path": "gone.txt"})

    @pytest.mark.parametrize("bad", ["../../../etc/passwd", "/etc/passwd", "a/../../../../etc/hosts"])
    def test_cannot_escape_workspace(self, tools, bad):
        with pytest.raises(ToolError, match="outside the workspace"):
            tools["file_ops"].handler({"op": "read", "path": bad})

    def test_cannot_write_outside_workspace(self, tools, tmp_path):
        target = tmp_path / "escaped.txt"
        with pytest.raises(ToolError):
            tools["file_ops"].handler({"op": "write", "path": str(target), "content": "x"})
        assert not target.exists()

    def test_symlink_escape_blocked(self, tools, sandboxed_env):
        link = sandboxed_env / "out"
        link.symlink_to("/etc")
        with pytest.raises(ToolError):
            tools["file_ops"].handler({"op": "read", "path": "out/passwd"})

    def test_refuses_recursive_delete(self, tools):
        fo = tools["file_ops"].handler
        fo({"op": "write", "path": "tree/child.txt", "content": "x"})
        with pytest.raises(ToolError, match="not empty"):
            fo({"op": "delete", "path": "tree"})


# -- memory_tool ------------------------------------------------------------
class TestMemory:
    def test_save_recall_search(self, tools):
        mt = tools["memory_tool"].handler
        mt({"op": "save", "key": "fact", "value": "the sky is blue"})
        assert mt({"op": "recall", "key": "fact"}) == "the sky is blue"
        assert "blue" in mt({"op": "search", "query": "sky"})

    def test_recall_missing(self, tools):
        assert "No memory" in tools["memory_tool"].handler({"op": "recall", "key": "absent"})

    def test_persists_across_instances(self, sandboxed_env):
        from nexus.memory import Memory

        with Memory() as m:
            m.save("k", "v1")
        with Memory() as m2:  # new connection, same file
            assert m2.recall("k") == "v1"


# -- code_exec --------------------------------------------------------------
class TestCodeExec:
    def test_runs_python(self, tools):
        assert tools["code_exec"].handler({"code": "print(6 * 7)"}).strip() == "42"

    def test_does_not_inherit_secrets(self, tools, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-SECRET")
        out = tools["code_exec"].handler(
            {"code": "import os; print(os.environ.get('ANTHROPIC_API_KEY'))"}
        )
        assert "SECRET" not in out
        assert out.strip() == "None"

    def test_timeout(self, tools):
        out = tools["code_exec"].handler({"code": "import time; time.sleep(10)", "timeout_seconds": 1})
        assert out.startswith("[TIMEOUT]")

    def test_cwd_is_workspace(self, tools, sandboxed_env):
        out = tools["code_exec"].handler({"code": "import os; print(os.getcwd())"}).strip()
        assert out == str(sandboxed_env.resolve())

    def test_writes_land_in_workspace(self, tools, sandboxed_env):
        tools["code_exec"].handler({"code": "open('made.txt','w').write('hi')"})
        assert (sandboxed_env / "made.txt").read_text() == "hi"

    def test_rejects_empty(self, tools):
        with pytest.raises(ToolError):
            tools["code_exec"].handler({"code": ""})


# -- http_request (local server) -------------------------------------------
class TestHttpRequest:
    def test_get(self, tools, http_server, allow_private):
        base, _ = http_server
        out = tools["http_request"].handler({"method": "GET", "url": base})
        assert "HTTP 200" in out and '"method": "GET"' in out

    def test_post_sends_body(self, tools, http_server, allow_private):
        base, handler = http_server
        out = tools["http_request"].handler({"method": "POST", "url": base, "json_body": {"a": 1}})
        assert "HTTP 200" in out
        assert json.loads(handler.received["body"]) == {"a": 1}

    def test_rejects_bad_method(self, tools):
        with pytest.raises(ToolError):
            tools["http_request"].handler({"method": "TRACE", "url": "http://x"})

    def test_guards_private_addresses_by_default(self, tools, http_server):
        # without allow_private, loopback must be refused
        base, _ = http_server
        with pytest.raises(ToolError, match="non-public"):
            tools["http_request"].handler({"method": "GET", "url": base})


# -- web_fetch (local server + pure functions) -----------------------------
class TestWebFetch:
    def test_fetch_and_extract(self, tools, http_server, allow_private):
        base, _ = http_server
        out = tools["web_fetch"].handler({"url": f"{base}/page"})
        assert "Heading" in out and "Para one." in out and "Para two." in out
        assert "var x=1" not in out  # script stripped
        assert "menu" not in out  # nav stripped

    def test_guards_private_by_default(self, tools, http_server):
        base, _ = http_server
        with pytest.raises(ToolError, match="non-public"):
            tools["web_fetch"].handler({"url": f"{base}/page"})

    def test_rejects_non_http_scheme(self, tools):
        with pytest.raises(ToolError, match="non-http"):
            tools["web_fetch"].handler({"url": "file:///etc/passwd"})

    def test_html_to_text_pure(self):
        from nexus.tools.web_fetch import html_to_text

        title, text = html_to_text("<title>T</title><body><h1>Hi</h1><p>x</p></body>")
        assert title == "T" and "Hi" in text and "x" in text


# -- web_search (pure parser, no network) ----------------------------------
class TestWebSearch:
    def test_ddg_parser_and_unwrap(self):
        import nexus.tools.web_search as ws

        body = (
            "<a class='result__a' href='//duckduckgo.com/l/?uddg="
            "https%3A%2F%2Fexample.com%2Fpage'>Title Here</a>"
            "<a class='result__snippet'>a snippet</a>"
        )
        parser = ws._DDGParser()
        parser.feed(body)
        parser.close()
        assert parser.results[0].url == "https://example.com/page"
        assert parser.results[0].title == "Title Here"
        assert parser.results[0].snippet == "a snippet"

    def test_empty_query_rejected(self, tools):
        with pytest.raises(ToolError):
            tools["web_search"].handler({"query": "  "})
