#!/usr/bin/env sh
#
# install.sh — Install the official Whop CLI (@whop/cli)
#
# Usage:
#   ./install.sh
#
# What it does:
#   - Verifies Node.js (>= 22) and npm are available
#   - Installs the official @whop/cli package globally from npm
#   - Verifies the install and prints next steps
#
# Notes:
#   The Whop CLI is published to npm as "@whop/cli"
#   (repo: https://github.com/whopio/whop-public-cli).
#   Installing from npm lets you review the package and its version
#   instead of piping a remote script straight into a shell.

set -eu

# --- Configuration ---------------------------------------------------------
PACKAGE="@whop/cli"
MIN_NODE_MAJOR=22

# --- Pretty output ---------------------------------------------------------
info()  { printf '\033[0;34m==>\033[0m %s\n' "$1"; }
ok()    { printf '\033[0;32m✓\033[0m %s\n' "$1"; }
warn()  { printf '\033[0;33m!\033[0m %s\n' "$1" >&2; }
error() { printf '\033[0;31m✗ %s\033[0m\n' "$1" >&2; }

fail() {
  error "$1"
  exit 1
}

# --- Preflight checks ------------------------------------------------------
info "Checking prerequisites…"

if ! command -v node >/dev/null 2>&1; then
  fail "Node.js is not installed. Install Node.js >= ${MIN_NODE_MAJOR} from https://nodejs.org and re-run this script."
fi

if ! command -v npm >/dev/null 2>&1; then
  fail "npm is not installed. It normally ships with Node.js — reinstall Node.js from https://nodejs.org."
fi

NODE_VERSION="$(node -v)"                       # e.g. v22.22.2
NODE_MAJOR="$(echo "$NODE_VERSION" | sed 's/^v//; s/\..*//')"

if [ "$NODE_MAJOR" -lt "$MIN_NODE_MAJOR" ]; then
  fail "Node.js ${NODE_VERSION} found, but the Whop CLI requires Node.js >= ${MIN_NODE_MAJOR}. Please upgrade."
fi

ok "Node.js ${NODE_VERSION} and npm $(npm -v) detected."

# --- Install ---------------------------------------------------------------
info "Installing ${PACKAGE} globally via npm…"

if npm install -g "$PACKAGE"; then
  ok "${PACKAGE} installed."
else
  fail "npm failed to install ${PACKAGE}. If this is a permissions error, either configure an npm global prefix you own (https://docs.npmjs.com/resolving-eacces-permissions-errors-when-installing-packages-globally) or re-run with elevated privileges."
fi

# --- Verify ----------------------------------------------------------------
if command -v whop >/dev/null 2>&1; then
  ok "Whop CLI ready: $(whop --version)"
  info "Next step: run 'whop quickstart' to get started."
else
  warn "Installed, but 'whop' is not on your PATH yet."
  warn "Add your npm global bin directory to PATH:"
  warn "  export PATH=\"\$(npm prefix -g)/bin:\$PATH\""
fi
