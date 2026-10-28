"""Run all three demos in order. This is what `docker compose run --rm app` does.

Each demo is also a standalone script, e.g.
    docker compose run --rm app python search_demo.py

Accepts the same options as the individual demos (--no-pause, --show-code, --list, ...).
"""

from cli import run
from kv_and_queries import DEMO as KV_AND_QUERIES
from search_demo import DEMO as SEARCH
from vector_demo import DEMO as VECTOR

if __name__ == "__main__":
    raise SystemExit(run([KV_AND_QUERIES, SEARCH, VECTOR]))
