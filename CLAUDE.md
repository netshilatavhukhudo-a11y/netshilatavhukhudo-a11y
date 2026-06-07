# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Purpose

This is a **GitHub profile repository** — the README.md is displayed publicly on the owner's GitHub profile page (`github.com/netshilatavhukhudo-a11y`). It contains no application code, build system, or dependencies. The sole deliverable is `README.md`.

## No Build or Test Commands

There are no package.json, Makefile, or build scripts. Nothing to install, compile, lint, or test. Changes take effect by committing and pushing `README.md`.

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

Active work happens on `claude/claude-md-docs-VnAYm`. Push changes there.
