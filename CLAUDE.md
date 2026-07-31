# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Purpose

This is a **GitHub profile repository** — the README.md is displayed publicly on the owner's GitHub profile page (`github.com/netshilatavhukhudo-a11y`). It has no application code, package manager, or build system.

It has three deliverables:

- `README.md` — the public profile page.
- Whop CLI setup tooling — `install.sh` (a POSIX `sh` installer for the `@whop/cli` npm package) and `SETUP.md` (how to run that CLI from Claude Code, covering the cloud network allowlist and `WHOP_API_KEY` authentication).
- Token launch tooling — `CLEAN_LAUNCH_PLAYBOOK.md` (a harm-reduction playbook for launching a memecoin without rugging) and `launchwatch.py` (a monitor that journals every trade, flags sniper wallets, and publicly logs creator-wallet sells as proof the creator is not selling). Not linked from `README.md` — it is deliberately kept off the public profile page.

## Build, Lint & Test Commands

There is no package.json, Makefile, or build script, and nothing to compile. Changes take effect by committing and pushing.

The two executable files **are** linted in CI, each on every push and pull request that touches a file of that type:

```sh
shellcheck --shell=sh install.sh   # .github/workflows/shellcheck.yml
ruff check .                       # .github/workflows/ruff.yml, ruff pinned to 0.16.1
```

Run the relevant one locally before pushing. There is no test suite.

`launchwatch.py` targets the stdlib plus `websockets` (imported lazily, so
`python launchwatch.py report <MINT>` works without it). It writes a
`launch.db` SQLite journal into the working directory; that file is
gitignored and must never be committed.

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
