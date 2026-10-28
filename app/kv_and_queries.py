"""
Demo 1: Key-Value operations and SQL++ queries
===============================================

Couchbase gives you two ways to work with the same JSON documents:

  1. KEY-VALUE (KV): fetch or change a document directly by its ID.
     This is the fastest path and is typically sub-millisecond.
  2. SQL++ (also called N1QL): the SQL you already know, extended for JSON.
     Use it to search, filter, join, and aggregate across documents.

This file walks through both using the Couchbase Python SDK 4.x. Read it top
to bottom - each step is small and commented.

Run it on its own with:
    docker compose run --rm app python kv_and_queries.py
"""
import couchbase.subdocument as SD
from couchbase.exceptions import DocumentNotFoundException

from common import (
    CB_BUCKET, SCOPE, banner, connect, console, keyspace, note, run_query,
    section, show_json, step, timed, why,
)


def key_value_demo(cluster):
    section("Part 1 · Key-Value operations")

    # Data lives at  bucket > scope > collection.  A "collection" handle is
    # all you need for KV operations.
    bucket = cluster.bucket(CB_BUCKET)
    airlines = bucket.scope(SCOPE).collection("airline")

    # ------------------------------------------------------------------ GET
    step("GET a document by its ID  (airline_10)")
    with timed("get") as t:
        result = airlines.get("airline_10")
    show_json("airline_10", result.content_as[dict])
    why("A KV get goes straight to the node that owns the document - no query "
        "planning, no index lookup - so reads like this are typically sub-millisecond.")

    # --------------------------------------------------------------- UPSERT
    step("UPSERT (create or replace) a new document")
    new_doc = {
        "type": "airline",
        "name": "Demo Air",
        "iata": "DM",
        "icao": "DMO",
        "callsign": "DEMO",
        "country": "United States",
    }
    with timed("upsert"):
        airlines.upsert("airline_demo_001", new_doc)
    note("Wrote airline_demo_001. Now reading it back:")
    with timed("get"):
        fetched = airlines.get("airline_demo_001")
    show_json("airline_demo_001", fetched.content_as[dict])
    why("Documents are just JSON - there is no schema to migrate first. "
        "Upsert makes writes simple: it creates the document or replaces it.")

    # ------------------------------------------------------------ SUB-DOC
    step("SUB-DOCUMENT operations: change one field without rewriting the whole document")
    with timed("mutate_in"):
        airlines.mutate_in(
            "airline_demo_001",
            [
                SD.upsert("callsign", "DEMO-AIR"),   # change an existing field
                SD.insert("founded", 2024),          # add a brand-new field
            ],
        )
    with timed("lookup_in"):
        # Read back ONLY these two fields, not the whole document.
        lookup = airlines.lookup_in(
            "airline_demo_001", [SD.get("callsign"), SD.get("founded")]
        )
    console.print(f"  callsign = {lookup.content_as[str](0)!r}, founded = {lookup.content_as[int](1)}")
    why("Sub-document operations send and receive only the fields you name. "
        "That saves network bandwidth and avoids read-modify-write races on big documents.")

    # --------------------------------------------------------------- REMOVE
    step("REMOVE the document, then handle 'not found' gracefully")
    with timed("remove"):
        airlines.remove("airline_demo_001")
    try:
        airlines.get("airline_demo_001")
    except DocumentNotFoundException:
        # The SDK raises specific, typed exceptions so your code can react to
        # exactly the situation you expect.
        console.print("  [green]✔ Caught DocumentNotFoundException - the document is gone, as expected.[/green]")
    why("Typed exceptions (not-found, already-exists, CAS mismatch...) let you write "
        "clear error handling instead of parsing error strings.")


def sql_plus_plus_demo(cluster):
    section("Part 2 · SQL++ queries")
    note("SQL++ is standard SQL extended for JSON. The statements below run against the same "
         "documents we just worked with using KV.")

    # 1 ------------------------------------------------------------ FILTER
    step("1. Simple filter - airlines in the United States")
    run_query(cluster, f"""
SELECT name, iata, callsign
FROM {keyspace('airline')}
WHERE country = "United States"
ORDER BY name
LIMIT 10""", columns=["name", "iata", "callsign"])
    why("If you know SQL, you already know how to query JSON in Couchbase. "
        "An index on country (created during setup) keeps this fast.")

    # 2 ------------------------------------------------------ NAMED PARAMS
    step("2. Parameterized query - airports in a city (named parameters)")
    run_query(cluster, f"""
SELECT airportname, city, country, faa
FROM {keyspace('airport')}
WHERE country = $country AND city = $city
ORDER BY airportname""", params={"country": "United States", "city": "San Francisco"},
        columns=["airportname", "city", "country", "faa"])
    why("Named parameters ($city, $country) keep user input out of the statement text, "
        "which prevents injection attacks, and let Couchbase reuse the query plan.")

    # 3 ------------------------------------------------------- AGGREGATION
    step("3. Aggregation with GROUP BY - which cities have the most hotels?")
    run_query(cluster, f"""
SELECT country, city, COUNT(*) AS hotels
FROM {keyspace('hotel')}
WHERE city IS NOT NULL
GROUP BY country, city
ORDER BY hotels DESC
LIMIT 8""", columns=["country", "city", "hotels"])
    why("Aggregations (COUNT, SUM, AVG, ...) work directly on operational JSON - "
        "no separate analytics copy of the data is needed for questions like this.")

    # 4 -------------------------------------------------------------- JOIN
    step("4. JOIN - routes out of San Francisco, with the carrier's name")
    run_query(cluster, f"""
SELECT a.name AS airline, r.sourceairport AS `from`, r.destinationairport AS `to`, r.stops
FROM {keyspace('route')} AS r
JOIN {keyspace('airline')} AS a ON KEYS r.airlineid
WHERE r.sourceairport = "SFO"
ORDER BY a.name, r.destinationairport
LIMIT 10""", columns=["airline", "from", "to", "stops"])
    why("Each route stores only the airline's document ID. A JOIN looks the airline up "
        "by key - the same fast path as a KV get - so you keep your data normalized "
        "without paying for it at read time.")

    # 5 ------------------------------------------------------------ UNNEST
    step("5. UNNEST - flatten each hotel's review array and average the ratings")
    run_query(cluster, f"""
SELECT h.name, h.city,
       COUNT(*) AS reviews,
       ROUND(AVG(r.ratings.Overall), 2) AS avg_rating
FROM {keyspace('hotel')} AS h
UNNEST h.reviews AS r
WHERE h.country = "United States" AND r.ratings.Overall IS NOT MISSING
GROUP BY h.name, h.city
HAVING COUNT(*) >= 5
ORDER BY avg_rating DESC, reviews DESC
LIMIT 8""", columns=["name", "city", "reviews", "avg_rating"])
    why("Reviews live INSIDE each hotel document as an array. UNNEST turns array items "
        "into rows so you can aggregate nested data without splitting it into extra tables.")

    # 6 ------------------------------------------------------------ LET/ARRAY
    step("6. LET and array expressions - best-rated hotels in France")
    run_query(cluster, f"""
SELECT h.name, h.city, avg_rating, ARRAY_LENGTH(h.reviews) AS reviews
FROM {keyspace('hotel')} AS h
LET ratings    = ARRAY r.ratings.Overall FOR r IN h.reviews WHEN r.ratings.Overall IS NOT MISSING END,
    avg_rating = ROUND(ARRAY_AVG(ratings), 2)
WHERE h.country = "France" AND ARRAY_LENGTH(ratings) >= 3
ORDER BY avg_rating DESC
LIMIT 5""", columns=["name", "city", "avg_rating", "reviews"])
    why("LET names an intermediate result so you can reuse it, and ARRAY ... FOR ... END "
        "builds or filters arrays in place - powerful JSON handling in plain SQL.")


def main():
    banner("Demo 1 · Key-Value operations & SQL++ queries",
           "Couchbase Python SDK 4.x  ·  travel-sample data set")
    cluster = connect()
    key_value_demo(cluster)
    sql_plus_plus_demo(cluster)
    console.print("\n[bold green]✔ Demo 1 complete.[/bold green]")


if __name__ == "__main__":
    main()
