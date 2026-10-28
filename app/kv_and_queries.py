"""Demo 1: Key-Value operations and SQL++ queries.

Couchbase gives you two ways to work with the same JSON documents:

  1. KEY-VALUE (KV): fetch or change a document directly by its ID. This is the
     fastest path and is typically sub-millisecond.
  2. SQL++ (also called N1QL): the SQL you already know, extended for JSON. Use it
     to search, filter, join and aggregate across documents.

Each step below is a short function that talks to Couchbase with the Python SDK 4.x
and returns what it found. Read it top to bottom.

    docker compose run --rm app python kv_and_queries.py
"""

import couchbase.subdocument as SD
from couchbase.exceptions import DocumentNotFoundException

from cli import run
from framework import Context, Demo, Result
from timing import Stopwatch

KV = "Part 1 · Key-Value operations"
SQLPP = "Part 2 · SQL++ queries"

DEMO = Demo(
    title="Demo 1 · Key-Value operations & SQL++ queries",
    subtitle="Couchbase Python SDK 4.x · travel-sample data set",
)

DEMO_KEY = "airline_demo_001"
DEMO_AIRLINE = {
    "type": "airline",
    "name": "Demo Air",
    "iata": "DM",
    "icao": "DMO",
    "callsign": "DEMO",
    "country": "United States",
}


def airlines(ctx: Context):
    """The `airline` collection. KV operations are methods on a collection."""
    return ctx.scope.collection("airline")


# --------------------------------------------------------------------------- Key-Value
@DEMO.step(
    "kv-get",
    "GET a document by its ID (airline_10)",
    section=KV,
    why="A KV get goes straight to the node that owns the document, with no query planning "
    "and no index lookup, so reads are typically sub-millisecond.",
)
def kv_get(ctx: Context) -> Result:
    with Stopwatch() as sw:
        document = airlines(ctx).get("airline_10")
    return Result(documents={"airline_10": document.content_as[dict]}, timings={"get": sw.ms})


@DEMO.step(
    "kv-upsert",
    "UPSERT (create or replace) a document, then read it back",
    section=KV,
    why="There is no schema to migrate first: documents are just JSON. Upsert creates the "
    "document or replaces it.",
)
def kv_upsert(ctx: Context) -> Result:
    collection = airlines(ctx)
    with Stopwatch() as upsert:
        collection.upsert(DEMO_KEY, DEMO_AIRLINE)
    with Stopwatch() as get:
        stored = collection.get(DEMO_KEY)
    return Result(
        documents={DEMO_KEY: stored.content_as[dict]},
        timings={"upsert": upsert.ms, "get": get.ms},
    )


@DEMO.step(
    "kv-subdoc",
    "SUB-DOCUMENT operations: change one field without rewriting the document",
    section=KV,
    why="Sub-document operations send and receive only the fields you name. That saves "
    "bandwidth and avoids read-modify-write races on large documents.",
)
def kv_subdoc(ctx: Context) -> Result:
    collection = airlines(ctx)
    collection.upsert(DEMO_KEY, DEMO_AIRLINE)  # setup, so this step can run on its own
    with Stopwatch() as mutate:
        collection.mutate_in(
            DEMO_KEY,
            [
                SD.upsert("callsign", "DEMO-AIR"),  # change an existing field
                SD.insert("founded", 2024),  # add a new field
            ],
        )
    with Stopwatch() as lookup:
        # Read back only these two fields, not the whole document.
        fields = collection.lookup_in(DEMO_KEY, [SD.get("callsign"), SD.get("founded")])
    return Result(
        lines=[f"callsign = {fields.content_as[str](0)!r}, founded = {fields.content_as[int](1)}"],
        timings={"mutate_in": mutate.ms, "lookup_in": lookup.ms},
    )


@DEMO.step(
    "kv-remove",
    "REMOVE the document, then handle 'not found'",
    section=KV,
    why="The SDK raises specific, typed exceptions (not found, already exists, CAS mismatch), "
    "so error handling does not depend on parsing messages.",
)
def kv_remove(ctx: Context) -> Result:
    collection = airlines(ctx)
    collection.upsert(DEMO_KEY, DEMO_AIRLINE)  # setup, so this step can run on its own
    with Stopwatch() as remove:
        collection.remove(DEMO_KEY)
    try:
        collection.get(DEMO_KEY)
    except DocumentNotFoundException:
        outcome = "✔ Caught DocumentNotFoundException: the document is gone, as expected."
    else:
        outcome = "✘ The document still exists."
    return Result(lines=[outcome], timings={"remove": remove.ms})


# ------------------------------------------------------------------------------ SQL++
@DEMO.step(
    "sql-filter",
    "Simple filter: airlines in the United States",
    section=SQLPP,
    why="If you know SQL you already know how to query JSON. An index on country, created "
    "during setup, keeps this fast.",
)
def sql_filter(ctx: Context) -> Result:
    return ctx.sqlpp(
        f"""
        SELECT name, iata, callsign
        FROM {ctx.keyspace("airline")}
        WHERE country = "United States"
        ORDER BY name
        LIMIT 10""",
        columns=["name", "iata", "callsign"],
    )


@DEMO.step(
    "sql-params",
    "Parameterized query: airports in a city (named parameters)",
    section=SQLPP,
    why="Named parameters keep user input out of the statement text, which prevents "
    "injection, and let the server reuse the query plan.",
)
def sql_params(ctx: Context) -> Result:
    return ctx.sqlpp(
        f"""
        SELECT airportname, city, country, faa
        FROM {ctx.keyspace("airport")}
        WHERE country = $country AND city = $city
        ORDER BY airportname""",
        {"country": "United States", "city": "San Francisco"},
        columns=["airportname", "city", "country", "faa"],
    )


@DEMO.step(
    "sql-group-by",
    "Aggregation: which cities have the most hotels? (GROUP BY)",
    section=SQLPP,
    why="Aggregates run directly on operational JSON, so questions like this need no "
    "separate analytics copy of the data.",
)
def sql_group_by(ctx: Context) -> Result:
    return ctx.sqlpp(
        f"""
        SELECT country, city, COUNT(*) AS hotels
        FROM {ctx.keyspace("hotel")}
        WHERE city IS NOT NULL
        GROUP BY country, city
        ORDER BY hotels DESC
        LIMIT 8""",
        columns=["country", "city", "hotels"],
    )


@DEMO.step(
    "sql-join",
    "JOIN: routes out of San Francisco, with each carrier's name",
    section=SQLPP,
    why="A route stores only its airline's document ID. ON KEYS looks the airline up by key, "
    "the same fast path as a KV get, so data stays normalized without a read-time penalty.",
)
def sql_join(ctx: Context) -> Result:
    return ctx.sqlpp(
        f"""
        SELECT a.name AS airline, r.sourceairport AS `from`, r.destinationairport AS `to`, r.stops
        FROM {ctx.keyspace("route")} AS r
        JOIN {ctx.keyspace("airline")} AS a ON KEYS r.airlineid
        WHERE r.sourceairport = "SFO"
        ORDER BY a.name, r.destinationairport
        LIMIT 10""",
        columns=["airline", "from", "to", "stops"],
    )


@DEMO.step(
    "sql-unnest",
    "UNNEST: flatten each hotel's reviews and average the ratings",
    section=SQLPP,
    why="Reviews live inside each hotel document as an array. UNNEST turns the array items "
    "into rows, so nested data can be aggregated without splitting it into more tables.",
)
def sql_unnest(ctx: Context) -> Result:
    return ctx.sqlpp(
        f"""
        SELECT h.name, h.city, COUNT(*) AS reviews, ROUND(AVG(r.ratings.Overall), 2) AS avg_rating
        FROM {ctx.keyspace("hotel")} AS h
        UNNEST h.reviews AS r
        WHERE h.country = "United States" AND r.ratings.Overall IS NOT MISSING
        GROUP BY h.name, h.city
        HAVING COUNT(*) >= 5
        ORDER BY avg_rating DESC, reviews DESC
        LIMIT 8""",
        columns=["name", "city", "reviews", "avg_rating"],
    )


@DEMO.step(
    "sql-let-array",
    "LET and array expressions: best-rated hotels in France",
    section=SQLPP,
    why="LET names an intermediate value so it can be reused, and ARRAY ... FOR ... END builds "
    "or filters arrays in place.",
)
def sql_let_array(ctx: Context) -> Result:
    return ctx.sqlpp(
        f"""
        SELECT h.name, h.city, avg_rating, ARRAY_LENGTH(h.reviews) AS reviews
        FROM {ctx.keyspace("hotel")} AS h
        LET ratings    = ARRAY r.ratings.Overall FOR r IN h.reviews
                         WHEN r.ratings.Overall IS NOT MISSING END,
            avg_rating = ROUND(ARRAY_AVG(ratings), 2)
        WHERE h.country = "France" AND ARRAY_LENGTH(ratings) >= 3
        ORDER BY avg_rating DESC
        LIMIT 5""",
        columns=["name", "city", "avg_rating", "reviews"],
    )


if __name__ == "__main__":
    raise SystemExit(run([DEMO]))
