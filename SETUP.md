# Running the Whop CLI from Claude Code

This document captures what it takes to install, authenticate, and operate the
[Whop CLI](https://github.com/whopio/whop-public-cli) (`whop`) from Claude Code —
both in a **cloud/remote session** (where outbound network access is restricted)
and on your **own machine** (where it just works).

The CLI itself is the npm package [`@whop/cli`](https://www.npmjs.com/package/@whop/cli)
(binary: `whop`). `install.sh` in this repo installs it via npm.

---

## TL;DR

| Need                                | Cloud session                                              | Local machine        |
| ----------------------------------- | --------------------------------------------------------- | -------------------- |
| Install the CLI                     | `npm install -g @whop/cli` (npm registry is allowed)      | any official method  |
| Reach `api.whop.com` / `whop.com`   | **must allowlist** via Custom network access              | works by default     |
| Authenticate                        | **API key** (`WHOP_API_KEY`) — browser OAuth can't finish | `whop` (OAuth) works |

The browser OAuth flow **cannot complete inside a cloud container**: the CLI's
callback lands on the container's `localhost`, which your browser can't reach.
Cloud sessions therefore authenticate with an API key; local sessions can use
either OAuth or an API key.

---

## Cloud-environment blueprint

Configure these **once** in the Claude Code web UI
(cloud icon → hover the environment → gear/settings). See
[Claude Code on the web → Network access](https://code.claude.com/docs/en/claude-code-on-the-web#allow-specific-domains).

### 1. Network access → Custom

The default **Trusted** level allows package registries (so `npm install` works)
but blocks `api.whop.com`. Switch to **Custom** and add:

```
api.whop.com
whop.com
```

- `api.whop.com` — every runtime command (`whop quickstart`, `whop products list`, …) calls this.
- `whop.com` — the `curl -fsSL https://whop.com/install.sh | sh` installer and checkout/docs URLs.
- **Keep "Also include default list of common package managers" checked**, or you
  lose npm/PyPI/GitHub access and `install.sh` stops working.

Symptom when this is missing:

```
HTTP_403: Host not in allowlist: api.whop.com
```

### 2. Environment variables

```
WHOP_API_KEY=whop_xxx
```

Create the key in your Whop dashboard under **Developer → API keys**. An API key
is tied to a **single business**; if you manage several, pass
`--account_id biz_xxx` to commands or create separate environments.

> **Security:** Cloud environments have **no dedicated secrets store** — env vars
> are visible to anyone who can edit the environment. A Whop key can carry
> payout / card-issuing / transfer scopes (real money). Scope keys to only what
> the task needs, keep a human in the loop for money-moving commands, and rotate
> or revoke keys regularly.

### 3. Setup script

Cloud containers are ephemeral, so reinstall the CLI on every session (the result
is cached, so it's fast after the first run):

```bash
npm install -g @whop/cli || true
```

### 4. Start a fresh session

Network egress policy is bound when a session **starts**, so edits to an existing
environment do **not** affect a session that's already running. Start a new
session for the allowlist to take effect.

Once the above is in place, a new session can run business commands immediately —
no login step, because `WHOP_API_KEY` is already set:

```bash
whop products list
whop stats time_series
whop apps deploy
```

---

## Local machine (simplest for interactive / money-touching work)

No egress proxy, so OAuth works and no API key needs to sit in a config field:

```bash
curl -fsSL https://whop.com/install.sh | sh   # prebuilt binary, no Node needed
# or: brew install whopio/tap/whop
# or: npm install -g @whop/cli                 # requires Node >= 22

whop            # no args: browser sign-in + choose/create business
whop --version
```

Recommendation: run **money-touching** CLI work locally (credentials stay on your
machine), and use cloud sessions for repo/dev work.

---

## Letting Claude drive the CLI

The Whop CLI is built to be agent-operated. Once installed and authenticated:

```bash
whop --llms     # machine-readable manifest of every command
whop mcp add    # register Whop as an MCP server Claude can call as tools
whop skills add # generate agent skills for common workflows
```

---

## Quick troubleshooting

| Symptom                                                     | Cause / fix                                                            |
| ----------------------------------------------------------- | --------------------------------------------------------------------- |
| `curl … whop.com/install.sh` → `403`                        | `whop.com` not allowlisted (or running in an already-started session) |
| `HTTP_403: Host not in allowlist: api.whop.com`             | Add `api.whop.com` to Custom network access; start a new session      |
| `NOT_AUTHENTICATED`                                         | Set `WHOP_API_KEY`, or run `whop` / `whop auth login` locally         |
| `NO_ACCOUNT: Couldn't resolve the business account`         | Usually a blocked/invalid key — verify network reach and key validity |
| OAuth login never completes in a cloud session              | Expected — use an API key in cloud; OAuth only works locally          |
| `whop: command not found` after install                     | `export PATH="$(npm prefix -g)/bin:$PATH"`                            |
