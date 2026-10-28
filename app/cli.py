"""Command-line entry point shared by every demo script.

python kv_and_queries.py                run Demo 1, pausing between steps
python kv_and_queries.py --no-pause     run straight through (used automatically
                                        when there is no keyboard, e.g. in CI)
python kv_and_queries.py --show-code    also print the source of each step
python kv_and_queries.py --list         list the steps
python kv_and_queries.py --only kv-get  run just that step
"""

import argparse
import logging
import sys
from collections.abc import Sequence

from client import connect
from config import get_settings
from framework import Context, Demo
from terminal import Options, TerminalRunner, interactive


def build_parser(demos: Sequence[Demo]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=" + ".join(d.title for d in demos))
    parser.add_argument(
        "--no-pause",
        action="store_true",
        help="do not wait for Enter between steps (the default when not at a keyboard)",
    )
    parser.add_argument(
        "--show-code", action="store_true", help="print the source code of each step before it runs"
    )
    parser.add_argument(
        "--rows", type=int, default=5, metavar="N", help="rows to show per table (default: 5)"
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="STEP",
        help="run only this step id (repeatable)",
    )
    parser.add_argument("--list", action="store_true", help="list the step ids and exit")
    return parser


def list_steps(demos: Sequence[Demo]) -> None:
    for demo in demos:
        print(demo.title)
        for step in demo.steps:
            print(f"  {step.id:<22} {step.title}")


def run(demos: Sequence[Demo], argv: Sequence[str] | None = None) -> int:
    """Run one or more demos in order. Returns the process exit code."""
    args = build_parser(demos).parse_args(argv)
    if args.list:
        list_steps(demos)
        return 0

    only = frozenset(args.only)
    known = {step.id for demo in demos for step in demo.steps}
    if unknown := only - known:
        print(f"Unknown step id(s): {', '.join(sorted(unknown))}. Use --list to see them.")
        return 2

    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    settings = get_settings()
    runner = TerminalRunner(
        Options(pause=interactive() and not args.no_pause, rows=args.rows, show_code=args.show_code)
    )
    ctx = Context(settings, connect(settings))
    ctx.warm_up()
    try:
        selected = [d for d in demos if not only or any(s.id in only for s in d.steps)]
        for number, demo in enumerate(selected):
            if number:
                runner.pause(f"Next: {demo.title}")
            runner.run(demo, ctx, only)
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return 130
    return 0
