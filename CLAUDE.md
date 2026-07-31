# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Purpose

This is a **GitHub profile repository** — the README.md is displayed publicly on the owner's GitHub profile page (`github.com/netshilatavhukhudo-a11y`). It has no application code, package manager, or build system.

It has three deliverables:

- `README.md` — the public profile page.
- Whop CLI setup tooling — `install.sh` (a POSIX `sh` installer for the `@whop/cli` npm package) and `SETUP.md` (how to run that CLI from Claude Code, covering the cloud network allowlist and `WHOP_API_KEY` authentication).
- `launchwatch/` — a standalone Python tool and its accompanying document: `launchwatch.py` (a read-only monitor that journals a token launch's trades to SQLite so creator-wallet activity is provable), `PLAYBOOK.md`, and a `README.md` for the directory. This is the only Python in the repo and the only part with a runtime dependency (`websockets`, pinned in `launchwatch/requirements.txt`). It is not referenced from the profile `README.md` — keep it that way unless asked.

## Build, Lint & Test Commands

There is no package.json, Makefile, or build script. Changes to the Markdown take effect by committing and pushing.

Both executable pieces **are** linted, each by its own workflow, on every push and pull request that touches a matching file. There is no test suite.

`install.sh` — CI runs ShellCheck (`.github/workflows/shellcheck.yml`):

```sh
shellcheck --shell=sh install.sh
```

`launchwatch/launchwatch.py` — CI runs a syntax check and Ruff (`.github/workflows/python.yml`):

```sh
python3 -m compileall -q launchwatch
ruff check .
```

Run the relevant command locally before pushing.

## README.md Structure & Conventions

The README uses a specific visual style — maintain it when editing:

- **Centered header block** (`<div align="center">`) with name, title, and shield.io badge links
- **Section dividers** using `---` horizontal rules between every major section
- **Badge style:** `style=for-the-badge` for contact/CTA badges; `style=flat` for tech stack badges
- **Badge color palette:** `#FF6B6B` (red-coral) is the accent color used across GitHub stats widgets
- **Tech table layout:** 2×2 HTML `<table>` with `valign="top" width="50%"` cells grouping related technologies
- **Project entries:** Each featured project follows: repo link as `### [\`Name\`](url)`, bold one-line description, `**Tech Stack:**` line, `**Key Features:**` line
- **Professional focus block:** Rendered as a `yaml` fenced code block

## Linked Projects

The README references two external repositories owned by the same user. Do not modify these links without confirmation:

- `TRIDENT-X AI` → `netshilatavhukhudo-a11y/trident-x-ai` — algorithmic trading engine (Python, WebSockets, PostgreSQL)
- `PipSense AI` → `netshilatavhukhudo-a11y/pipsense-ai` — market signal platform (Python, Claude API, PostgreSQL)

## Development Branch

The default branch is `main`. Do not commit to it directly — work on a feature
branch and open a pull request against `main`, which is how every change in this
repo's history has landed. Naming a specific working branch here goes stale as
soon as that branch merges, so check `git branch` for the branch in play.
