"""
Shared helpers for the Couchbase demos.

Every demo imports from here so that the demo files themselves can stay short
and focus on the interesting part: how you talk to Couchbase.

This file does two things:
  1. Reads settings from environment variables and opens a connection.
  2. Provides small "pretty print" helpers (headings, timings, tables) so the
     console output explains itself without a presenter.
"""
import os
import time
from contextlib import contextmanager
from datetime import timedelta

from couchbase.auth import PasswordAuthenticator
from couchbase.cluster import Cluster
from couchbase.options import ClusterOptions, ClusterTimeoutOptions
from rich.console import Console
from rich.padding import Padding
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

console = Console(highlight=False)

# --- 1. Configuration --------------------------------------------------------
# Nothing is hard-coded. To point these demos at a different cluster (for
# example a Couchbase Capella free trial), just change these environment
# variables. See docs/run-locally.md.
CB_CONNSTR = os.environ.get("CB_CONNSTR", "couchbase://localhost")
CB_USER = os.environ["CB_USER"]
CB_PASSWORD = os.environ["CB_PASSWORD"]
CB_BUCKET = os.environ.get("CB_BUCKET", "travel-sample")

# In Couchbase, data is organised as  bucket > scope > collection  (a bit like
# database > schema > table). travel-sample keeps its data in the "inventory"
# scope, with collections such as airline, airport, route and hotel.
SCOPE = "inventory"


def keyspace(collection: str) -> str:
    """Full SQL++ name of a collection, e.g. `travel-sample`.inventory.hotel"""
    return f"`{CB_BUCKET}`.{SCOPE}.{collection}"


def connect(user: str | None = None, password: str | None = None) -> Cluster:
    """Open a connection to the cluster and wait until it is ready.

    By default this logs in as the limited demo user. (Only the one-time
    embedding step passes administrator credentials, because it creates indexes.)

    The SDK discovers every node and service on its own from the address you
    give it, then talks directly to the right node for each request.
    """
    options = ClusterOptions(
        PasswordAuthenticator(user or CB_USER, password or CB_PASSWORD),
        # Be patient on the very first connection (a freshly started server
        # can take a few seconds to hand out its cluster map).
        timeout_options=ClusterTimeoutOptions(connect_timeout=timedelta(seconds=20)),
    )
    last_error = None
    for attempt in range(1, 4):
        try:
            cluster = Cluster(CB_CONNSTR, options)
            cluster.wait_until_ready(timedelta(seconds=30))
            return cluster
        except Exception as exc:  # noqa: BLE001 - retry any connect failure
            last_error = exc
            console.print(f"[dim]Could not connect yet (attempt {attempt}/3), retrying...[/dim]")
            time.sleep(3)
    raise SystemExit(f"Could not connect to Couchbase at {CB_CONNSTR}: {last_error}")


# --- 2. Pretty-printing helpers ---------------------------------------------
def banner(title: str, subtitle: str = "") -> None:
    """Big heading at the start of a demo."""
    body = f"[bold]{title}[/bold]" + (f"\n[dim]{subtitle}[/dim]" if subtitle else "")
    console.print()
    console.print(Panel(body, border_style="red", padding=(1, 2)))


def section(title: str) -> None:
    """Heading for a group of related steps."""
    console.print()
    console.rule(f"[bold red]{title}[/bold red]", align="left")


def step(title: str) -> None:
    """Heading for one individual step."""
    console.print()
    console.print(f"[bold cyan]▶ {title}[/bold cyan]")


def why(text: str) -> None:
    """The one-line 'why this matters' explanation shown after a step."""
    console.print(Padding(f"[yellow]💡 Why this matters:[/yellow] {text}", (0, 2)))


def note(text: str) -> None:
    console.print(Padding(f"[dim]{text}[/dim]", (0, 2)))


def show_sql(statement: str, params: dict | None = None) -> None:
    """Print a SQL++ statement (and any named parameters) with syntax colours."""
    console.print(Syntax(statement.strip(), "sql", theme="ansi_dark", word_wrap=True, padding=(0, 2)))
    if params:
        console.print(f"  [dim]parameters: {params}[/dim]")


def show_json(label: str, doc) -> None:
    import json
    text = json.dumps(doc, indent=2, default=str)
    console.print(f"  [bold]{label}[/bold]")
    console.print(Syntax(text, "json", theme="ansi_dark", padding=(0, 2)))


def show_table(rows: list[dict], max_rows: int = 10, title: str | None = None) -> None:
    """Print a list of dicts as a table, truncating long lists."""
    if not rows:
        console.print("  [dim](no rows)[/dim]")
        return
    table = Table(title=title, header_style="bold", pad_edge=False, box=None, padding=(0, 2))
    columns = list(rows[0].keys())
    for col in columns:
        table.add_column(str(col))
    for row in rows[:max_rows]:
        table.add_row(*[_fmt(row.get(c)) for c in columns])
    console.print(table)
    if len(rows) > max_rows:
        console.print(f"  [dim]... and {len(rows) - max_rows} more rows[/dim]")


def _fmt(value) -> str:
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    text = "" if value is None else str(value)
    return text if len(text) <= 70 else text[:67] + "..."


@contextmanager
def timed(label: str = ""):
    """Time a block of code. Use:  with timed() as t: ...  then read t.ms"""
    class Timer:
        ms = 0.0
    t = Timer()
    start = time.perf_counter()
    try:
        yield t
    finally:
        t.ms = (time.perf_counter() - start) * 1000
        if label:
            console.print(f"  ⏱  {label}: [bold green]{t.ms:.1f} ms[/bold green]")


def run_query(cluster: Cluster, statement: str, params: dict | None = None,
              max_rows: int = 10, columns: list[str] | None = None,
              display_params: dict | None = None):
    """Run a SQL++ statement and print results, row count and timings.

    Two timings are shown:
      * server time  - how long the Query service spent executing it
      * round trip   - total time as seen by this application, network included
    """
    from couchbase.options import QueryOptions

    # display_params lets a caller show a short stand-in for a huge parameter
    # (such as a 384-number vector) while still sending the real value.
    show_sql(statement, display_params or params)
    start = time.perf_counter()
    # metrics=True asks the server to report how long it spent on the query.
    result = cluster.query(statement, QueryOptions(named_parameters=params or {}, metrics=True))
    rows = list(result)
    round_trip_ms = (time.perf_counter() - start) * 1000
    server_ms = result.metadata().metrics().execution_time().total_seconds() * 1000

    # Couchbase returns JSON objects with their keys sorted alphabetically, so
    # we pass `columns` to show them in the same order as the SELECT list.
    if columns:
        rows = [{c: row.get(c) for c in columns} for row in rows]
    show_table(rows, max_rows=max_rows)
    console.print(
        f"  [green]{len(rows)} row(s)[/green]  ⏱  server [bold green]{server_ms:.1f} ms[/bold green]"
        f" · round trip [bold green]{round_trip_ms:.1f} ms[/bold green]"
    )
    return rows
