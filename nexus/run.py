#!/usr/bin/env python3
"""NEXUS CLI entrypoint.

    python run.py "your goal here"
    python run.py --show-config
"""

from __future__ import annotations

import argparse
import json
import sys

from nexus import ConfigError, load_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="NEXUS — a general-purpose autonomous agent.",
    )
    parser.add_argument("goal", nargs="?", help="natural-language goal to pursue")
    parser.add_argument(
        "--show-config",
        action="store_true",
        help="print the resolved config and exit",
    )
    parser.add_argument("--config", help="path to an alternate agent.json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    if args.show_config or not args.goal:
        print(json.dumps(config, indent=2))
        if not args.goal and not args.show_config:
            print("\nno goal given. usage: python run.py \"your goal\"", file=sys.stderr)
            return 1
        return 0

    print("orchestrator not wired yet — see Phase 1.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
