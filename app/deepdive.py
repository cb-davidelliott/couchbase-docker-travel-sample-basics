"""Deep-dive tools for engineers who want to look under the hood.

  docker compose run --rm app python deepdive.py explain "<SQL++ statement>"
  docker compose run --rm app python deepdive.py bench --runs 200
  docker compose run --rm app python deepdive.py recall --k 10

explain  show the query plan: which indexes a SQL++ statement uses
bench    latency percentiles (p50 / p95 / p99), not a single lucky timing
recall   how close the approximate vector indexes get to the exact answer
"""

import argparse
import contextlib
import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import couchbase.subdocument as SD
import numpy as np
from couchbase.exceptions import DocumentNotFoundException
from couchbase.options import QueryOptions, SearchOptions
from couchbase.search import MatchQuery, SearchRequest
from couchbase.vector_search import VectorQuery, VectorSearch
from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

from client import connect
from config import TEXT_INDEX, VECTOR_INDEX, get_settings
from embeddings import embed_text, embed_texts
from framework import Context
from timing import Stopwatch, percentiles

console = Console(highlight=False)

WARMUP_RUNS = 10
BENCH_KEY = "bench_doc"
GSI_VECTOR_INDEXES = ("idx_hotel_composite", "idx_hotel_hyperscale")

# Natural-language questions used to measure vector recall.
RECALL_QUESTIONS = (
    "quiet beachfront place good for families with young kids",
    "somewhere to unwind after a long day hiking in the mountains",
    "a romantic getaway with a view of the sea",
    "cheap hostel for backpackers near the city centre",
    "luxury hotel with a spa and fine dining",
    "pet friendly place with a garden",
    "business hotel close to the airport with fast wifi",
    "historic building in the old town",
    "campsite by a lake for a tent holiday",
    "modern apartment with a kitchen for a long stay",
    "family run bed and breakfast in the countryside",
    "hotel near public transport and shopping",
)


# ------------------------------------------------------------------------------ explain
@dataclass(frozen=True)
class PlanSummary:
    indexes: list[str]
    operators: list[str]


def summarize_plan(plan: Any) -> PlanSummary:
    """List the indexes and operators in a SQL++ query plan, in the order they appear."""
    indexes: list[str] = []
    operators: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            operator = node.get("#operator")
            if operator and operator not in operators:
                operators.append(operator)
            index = node.get("index")
            if isinstance(index, str) and index not in indexes:
                indexes.append(index)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(plan)
    return PlanSummary(indexes, operators)


def explain(ctx: Context, args: argparse.Namespace) -> None:
    row = next(iter(ctx.cluster.query(f"EXPLAIN {args.statement}")))
    summary = summarize_plan(row["plan"])
    console.print(Syntax(args.statement.strip(), "sql", theme="ansi_dark", word_wrap=True))
    console.print(
        f"\n[bold]Indexes used:[/bold] {', '.join(summary.indexes) or '(none: full scan)'}"
    )
    console.print(f"[bold]Operators:[/bold]    {' → '.join(summary.operators)}")
    if args.json:
        console.print(Syntax(json.dumps(row["plan"], indent=2), "json", theme="ansi_dark"))


# --------------------------------------------------------------------------------- bench
def operations(ctx: Context) -> dict[str, Callable[[], object]]:
    """The operations to time. Each one does a complete round trip and reads all results."""
    settings = ctx.settings
    airlines = ctx.scope.collection("airline")
    vector = embed_text(RECALL_QUESTIONS[0])  # embed once: we time Couchbase, not the model

    def sqlpp(statement: str, **parameters: Any) -> Callable[[], object]:
        options = QueryOptions(named_parameters=parameters)
        return lambda: list(ctx.cluster.query(statement, options))

    return {
        "KV get": lambda: airlines.get("airline_10"),
        "KV sub-document lookup": lambda: airlines.lookup_in("airline_10", [SD.get("callsign")]),
        "KV upsert": lambda: airlines.upsert(BENCH_KEY, {"type": "bench", "n": 1}),
        "SQL++ indexed filter": sqlpp(
            f"SELECT airportname FROM {settings.keyspace('airport')} "
            "WHERE country = $country AND city = $city",
            country="United States",
            city="San Francisco",
        ),
        "SQL++ join": sqlpp(
            f"SELECT a.name FROM {settings.keyspace('route')} AS r "
            f"JOIN {settings.keyspace('airline')} AS a ON KEYS r.airlineid "
            "WHERE r.sourceairport = $airport LIMIT 10",
            airport="SFO",
        ),
        "Search match": lambda: list(
            ctx.scope.search(
                TEXT_INDEX,
                SearchRequest.create(MatchQuery("beach", field="description")),
                SearchOptions(limit=10),
            )
        ),
        "Search vector (k=10)": lambda: list(
            ctx.scope.search(
                VECTOR_INDEX,
                SearchRequest.create(
                    VectorSearch.from_vector_query(
                        VectorQuery("embedding", vector, num_candidates=10)
                    )
                ),
                SearchOptions(limit=10),
            )
        ),
        "SQL++ vector, Composite (k=10)": sqlpp(
            f"SELECT h.name FROM {settings.keyspace('hotel')} AS h "
            "USE INDEX (idx_hotel_composite USING GSI) "
            'ORDER BY APPROX_VECTOR_DISTANCE(h.embedding, $qvec, "COSINE") LIMIT 10',
            qvec=vector,
        ),
    }


def measure(operation: Callable[[], object], runs: int) -> list[float]:
    """Run an operation `runs` times after a short warm-up; return each latency in ms."""
    for _ in range(WARMUP_RUNS):
        operation()
    samples = []
    for _ in range(runs):
        with Stopwatch() as sw:
            operation()
        samples.append(sw.ms)
    return samples


def bench(ctx: Context, args: argparse.Namespace) -> None:
    table = Table(title=f"Latency in ms over {args.runs} runs (after {WARMUP_RUNS} warm-up runs)")
    table.add_column("operation")
    for column in ("p50", "p95", "p99", "max"):
        table.add_column(column, justify="right")
    try:
        for name, operation in operations(ctx).items():
            stats = percentiles(measure(operation, args.runs))
            table.add_row(name, *(f"{stats[c]:.2f}" for c in ("p50", "p95", "p99", "max")))
    finally:
        with contextlib.suppress(DocumentNotFoundException):
            ctx.scope.collection("airline").remove(BENCH_KEY)
    console.print(table)
    console.print(
        "\n[dim]Conditions: one single-node Couchbase container, one client thread issuing "
        "requests one at a time, client and server on the same machine. This shows the cost of "
        "one round trip, not production throughput or latency under load.[/dim]"
    )


# ------------------------------------------------------------------------------- recall
def exact_top_k(
    matrix: np.ndarray, ids: Sequence[str], query: Sequence[float], k: int
) -> list[str]:
    """The true k nearest documents by brute force. Vectors are unit length, so a dot
    product is the cosine similarity."""
    scores = matrix @ np.asarray(query)
    return [ids[i] for i in np.argsort(-scores)[:k]]


def recall_at_k(exact: Sequence[str], approximate: Sequence[str]) -> float:
    """The fraction of the true top-k that the approximate search also returned."""
    return len(set(exact) & set(approximate)) / len(exact)


def load_embeddings(ctx: Context) -> tuple[list[str], np.ndarray]:
    rows = list(
        ctx.cluster.query(
            f"SELECT META(h).id AS id, h.embedding FROM {ctx.keyspace('hotel')} AS h "
            "WHERE h.embedding IS NOT MISSING"
        )
    )
    return [r["id"] for r in rows], np.array([r["embedding"] for r in rows])


def recall(ctx: Context, args: argparse.Namespace) -> None:
    ids, matrix = load_embeddings(ctx)
    questions = embed_texts(list(RECALL_QUESTIONS))
    probes = f", {args.nprobes}" if args.nprobes else ""

    def search_index(vector: list[float]) -> list[str]:
        query = VectorQuery("embedding", vector, num_candidates=args.k)
        request = SearchRequest.create(VectorSearch.from_vector_query(query))
        return [
            hit.id for hit in ctx.scope.search(VECTOR_INDEX, request, SearchOptions(limit=args.k))
        ]

    def gsi_index(index: str) -> Callable[[list[float]], list[str]]:
        statement = (
            f"SELECT RAW META(h).id FROM {ctx.keyspace('hotel')} AS h "
            f"USE INDEX ({index} USING GSI) "
            f'ORDER BY APPROX_VECTOR_DISTANCE(h.embedding, $qvec, "COSINE"{probes}) LIMIT {args.k}'
        )
        return lambda vector: list(
            ctx.cluster.query(statement, QueryOptions(named_parameters={"qvec": vector}))
        )

    retrievers = {f"Search vector index ({VECTOR_INDEX})": search_index}
    retrievers.update({f"GSI {name}": gsi_index(name) for name in GSI_VECTOR_INDEXES})

    table = Table(
        title=f"Recall@{args.k} vs exact search ({len(ids)} hotels, {len(questions)} questions)"
    )
    for column, justify in (
        ("index", "left"),
        ("mean recall", "right"),
        ("worst", "right"),
        ("mean ms", "right"),
    ):
        table.add_column(column, justify=justify)
    for name, retrieve in retrievers.items():
        scores, latencies = [], []
        for vector in questions:
            with Stopwatch() as sw:
                found = retrieve(vector)
            latencies.append(sw.ms)
            scores.append(recall_at_k(exact_top_k(matrix, ids, vector, args.k), found))
        table.add_row(
            name, f"{np.mean(scores):.3f}", f"{min(scores):.2f}", f"{np.mean(latencies):.1f}"
        )
    console.print(table)
    console.print(
        f"\n[dim]Recall@{args.k} = the share of the true {args.k} nearest hotels (found by "
        "brute force over every embedding) that the index also returned. 1.000 means identical. "
        f"GSI centroids probed: {args.nprobes or 'server default'} (on a set this small the "
        "indexes have very few centroids, so the setting changes little here; "
        "it matters at scale).[/dim]"
    )


# --------------------------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)

    explain_parser = commands.add_parser("explain", help="show the plan for a SQL++ statement")
    explain_parser.add_argument(
        "statement", help="a SQL++ statement; name collections as `travel-sample`.inventory.hotel"
    )
    explain_parser.add_argument(
        "--json", action="store_true", help="also print the full plan as JSON"
    )
    explain_parser.set_defaults(handler=explain)

    bench_parser = commands.add_parser("bench", help="latency percentiles for typical operations")
    bench_parser.add_argument(
        "--runs", type=int, default=200, help="measured runs per operation (default: 200)"
    )
    bench_parser.set_defaults(handler=bench)

    recall_parser = commands.add_parser("recall", help="vector index recall against exact search")
    recall_parser.add_argument(
        "--k", type=int, default=10, help="neighbours to retrieve (default: 10)"
    )
    recall_parser.add_argument(
        "--nprobes", type=int, help="GSI centroids to probe (default: server default)"
    )
    recall_parser.set_defaults(handler=recall)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    settings = get_settings()
    args.handler(Context(settings, connect(settings)), args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
