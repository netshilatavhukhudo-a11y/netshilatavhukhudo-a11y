#!/usr/bin/env python3
"""NEXUS CLI entrypoint.

    python run.py "What is the current version of Playwright?"
    python run.py --show-config
    python run.py --list-tools

The final answer goes to stdout; the live trace goes to stderr. So this works:

    python run.py "..." > answer.md
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
    parser.add_argument("--config", help="path to an alternate agent.json")
    parser.add_argument(
        "--show-config", action="store_true", help="print the resolved config and exit"
    )
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="load the enabled tools, print their schemas and tiers, and exit",
    )
    return parser


# Exit codes, so NEXUS composes in a shell pipeline.
_EXIT: dict[str, int] = {
    "done": 0,
    "max_steps": 3,
    "timeout": 4,
    "budget": 5,
    "aborted": 130,
    "refused": 6,
    "error": 7,
}


def _list_tools(config: dict) -> int:
    from nexus.permissions import resolve_tier
    from nexus.registry import export_schemas, load_tools

    loaded, skipped = load_tools(config["enabled_tools"])
    for schema in export_schemas(loaded):
        tier = resolve_tier(schema["name"], {}, config).tier
        print(f"\n=== {schema['name']}  [tier: {tier}] ===")
        print(schema["description"])
        print(json.dumps(schema["input_schema"], indent=2))
    for name, reason in skipped.items():
        print(f"\n=== {name}  [UNAVAILABLE] ===\n{reason}", file=sys.stderr)
    print(f"\n{len(loaded)} tool(s) loaded, {len(skipped)} unavailable.", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    if args.show_config:
        print(json.dumps(config, indent=2))
        return 0

    if args.list_tools:
        return _list_tools(config)

    if not args.goal or not args.goal.strip():
        print('usage: python run.py "your goal"', file=sys.stderr)
        return 1

    # Imported late so --show-config and --list-tools work without an API key.
    from nexus.orchestrator import run
    from nexus.tracing import start_run

    tracer = start_run(args.goal, config)
    try:
        result = run(args.goal, config)
    except KeyboardInterrupt:
        # The kill switch. The trace is already flushed line-by-line on disk.
        print(f"\naborted by operator. trace: {tracer.path}", file=sys.stderr)
        return 130
    finally:
        tracer.close()

    print(result.answer)
    print(
        f"\nstatus={result.status} steps={result.steps} elapsed={result.elapsed_s}s "
        f"tokens={result.usage.get('total', 0)}/{result.usage.get('limit', 0)}\n"
        f"trace: {tracer.path}",
        file=sys.stderr,
    )
    return _EXIT.get(result.status, 7)


if __name__ == "__main__":
    raise SystemExit(main())
