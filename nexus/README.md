# NEXUS

A general-purpose autonomous agent. Give it a natural-language goal; it
decomposes the goal and pursues it by calling tools in a
perceive → plan → act → observe → reflect loop until the goal is met or a stop
condition fires.

The whole system is one idea: **an LLM that can call functions, in a loop,
with memory and guardrails.**

```
GOAL ─▶ [ORCHESTRATOR] ─▶ ask LLM: "what next?"
              ▲                    │
              │              tool_use? ──yes──▶ [PERMISSION CHECK] ─▶ [EXECUTOR] ─▶ tool result
              │                    │                                                    │
              └──── append result, loop ◀───────────────────────────────────────────────┘
                                   │
                              stop_reason=end_turn? ──▶ DONE, return final answer
```

Everything else — browsing, code execution, HTTP calls — is just a tool the
LLM is allowed to pick.

## Design principles

- **Extensible.** Adding a capability = dropping one file in `nexus/tools/` and
  adding its name to `enabled_tools`. No core change. (Proven: the `calculator`
  tool is ~25 lines and touches nothing else.)
- **Observable.** Every thought, tool call, tool result, and permission
  decision is written to a structured JSONL trace and rendered live on stderr.
- **Safe by default.** Irreversible or money-touching actions require explicit,
  typed human approval. The LLM can never override a permission tier.
- **Model-agnostic brain.** Swap the model with one config value.

## Setup

Requires Python 3.11+.

```sh
cd nexus
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# For the browser tool (Phase 3+):
python -m playwright install chromium

cp .env.example .env      # then edit .env — see keys below
```

### `.env` keys

| Key | Required | Purpose |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | **yes** | The agent's brain. From https://console.anthropic.com |
| `BRAVE_API_KEY` | no | Preferred `web_search` provider. |
| `SERPER_API_KEY` | no | Alternative `web_search` provider (Google). |
| `NEXUS_CONFIG` | no | Path to an alternate `agent.json` (default `config/agent.json`). |
| `NEXUS_WORKSPACE` | no | Sandbox root for `file_ops`/`code_exec` (default `workspace/`). |
| `NEXUS_TRACE_DIR` | no | Where JSONL traces are written (default `traces/`). |
| `NEXUS_NONINTERACTIVE` | no | Set to `1` in CI/cron so approval prompts auto-**deny** instead of hanging. There is no auto-approve, by design. |
| `NEXUS_ALLOW_PRIVATE_NETWORK` | no | Set to `1` to let the web/HTTP/browser tools reach private or loopback addresses (off by default — a hallucinated hostname should not be able to reach the cloud metadata endpoint or the host network). |
| `NEXUS_CHROMIUM_PATH` | no | Explicit Chromium binary, if the pip Playwright version and the installed browser build differ. |

No secrets live in the repo. `.env` is gitignored; `.env.example` lists every
key with a placeholder.

## Running

```sh
python run.py "What is the current version of Playwright and summarize its changelog."
```

The final answer goes to **stdout**; the live trace goes to **stderr**, so you
can capture just the answer:

```sh
python run.py "..." > answer.md
```

Other entry points:

```sh
python run.py --show-config     # print the resolved config and exit
python run.py --list-tools      # load the enabled tools, show schemas + tiers
```

Exit code reflects the outcome (`0` done, `3` max steps, `4` timeout, `5`
budget, `6` refused, `7` error, `130` aborted), so NEXUS composes in a shell.

**Kill switch:** Ctrl-C cleanly aborts the run and leaves the trace on disk
(it is flushed per event, so nothing is lost even on a hard kill).

## Tools

| Tool | Default tier | What it does |
| --- | --- | --- |
| `web_search` | auto | Search the web (Brave / Serper / DuckDuckGo). |
| `web_fetch` | auto | Fetch a URL and return cleaned readable text. |
| `browser` | read: auto, submit_form: confirm | Drive headless Chromium: goto, click, fill, extract_text, screenshot. |
| `code_exec` | confirm | Run Python in an isolated subprocess sandbox. |
| `http_request` | GET: auto, POST/PUT/DELETE: confirm | Call any HTTP API. |
| `file_ops` | read: auto, write: confirm, delete: block | Read/write/list/delete files in the workspace. |
| `memory_tool` | auto | Persist and recall facts across steps and sessions. |
| `calculator` | auto | Evaluate an arithmetic expression (the 8th-tool demo). |

## Permission tiers

Defined in `config/agent.json` under `permissions.tool_tiers`, enforced in
`nexus/permissions.py`:

- **`auto`** — reversible / read-only. Runs freely, no approval.
- **`confirm`** — writes external state but reversible. Requires a `y`/`yes`.
- **`block`** — irreversible, money movement, or live-price changes. Requires a
  typed `APPROVE`, shown with full detail of the action, **every single time** —
  no "approve all", no session memory.

Two invariants hold no matter what the model outputs:

1. Tiers come from config, never from the model.
2. When several config keys match a call, the **strictest** wins — so a
   creatively-shaped tool input (a missing or misspelled `op`, say) can only
   ever land on a *stricter* tier, never a weaker one.

The gate **fails closed**: with no terminal to ask on (CI, cron), any
confirm/block action is denied, not run.

## Adding a tool

The entire extension mechanism, in three steps:

1. Write `nexus/tools/<name>.py`:

   ```python
   from nexus.tools.base import ToolError, tool

   @tool(
       "my_tool",
       "One-line description the LLM reads to decide when to use this.",
       {"type": "object",
        "properties": {"arg": {"type": "string"}},
        "required": ["arg"]},
       tier_key="my_tool",
   )
   def my_tool(inp: dict) -> str:
       arg = inp.get("arg")
       if not arg:
           raise ToolError("my_tool requires 'arg'")
       return f"did something with {arg}"
   ```

2. Add `"my_tool"` to `enabled_tools` in `config/agent.json`.
3. Add a tier for it under `permissions.tool_tiers` (omit it to use
   `default_tier`).

Nothing in `orchestrator.py`, `registry.py`, `llm.py`, `permissions.py`, or
`tracing.py` changes. See `nexus/tools/calculator.py` for a real, tested
example done in one file.

## Sandboxing notes

`code_exec` never uses `exec()` in-process. It runs generated code in a
separate process with:

- an empty **network namespace** (`unshare -n`) where the kernel allows it — the
  tool reports when isolation is unavailable rather than pretending;
- **resource limits** on address space, CPU time, file size, and process count;
- a **scrubbed environment** that does not inherit `ANTHROPIC_API_KEY` or any
  other secret;
- `python -I` (isolated mode) and a cwd pinned to the workspace;
- process-group kill on timeout.

For untrusted or hostile code, run NEXUS itself inside a container or a proper
jail (firejail/nsjail) — the subprocess sandbox is a strong floor, not a
replacement for OS-level isolation.

`file_ops`, `code_exec`, and `memory` are all confined to the workspace
directory; paths that resolve outside it (via `..`, absolute paths, or
symlinks) are refused.

## Tests

```sh
python -m pytest
```

The suite covers config loading/validation, the orchestrator loop (with a
scripted brain patched in at the transport boundary — no API key or network
needed), permission tiers and the human gate, each tool individually, and the
extension mechanism. Browser tests skip gracefully if Chromium can't launch.

`install.sh` is unrelated to NEXUS — it belongs to the repository's Whop CLI
tooling.

## Layout

```
nexus/
├── config/agent.json       # identity, limits, permission tiers — no magic numbers in code
├── nexus/
│   ├── __init__.py         # config loader + workspace resolver
│   ├── orchestrator.py     # the loop
│   ├── llm.py              # Anthropic Messages API wrapper
│   ├── registry.py         # tool discovery + schema export
│   ├── permissions.py      # tier checks + human approval gate
│   ├── memory.py           # SQLite get/set/search (vector-ready interface)
│   ├── tracing.py          # structured JSONL logger + rich console
│   └── tools/              # one file per capability
├── run.py                  # CLI entrypoint
└── tests/                  # loop + every tool
```
