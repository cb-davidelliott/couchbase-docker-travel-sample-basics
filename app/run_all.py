"""Run all three demos in order. This is what `docker compose run --rm app` does.

Each demo is also a standalone script, e.g.
    docker compose run --rm app python search_demo.py

Accepts the same options as the individual demos (--no-pause, --show-code, --list, ...).
"""

from catalog import DEMOS
from cli import run

if __name__ == "__main__":
    raise SystemExit(run(DEMOS))
