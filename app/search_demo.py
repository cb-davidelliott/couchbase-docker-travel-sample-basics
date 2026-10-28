"""
Demo 2: Full Text Search
========================

Couchbase has a built-in Search service (Full Text Search, or "FTS"). It
understands language: it breaks text into words, handles plurals and
typos, ranks results by relevance, highlights matches, and counts results
by category (facets).

Search works on an index that the setup step already built for you. You can
read its definition in  init/search/hotel_text.json  - it tells Couchbase
which hotel fields to index and how to analyze each one.

You query it from the Python SDK with the same connection as every other
demo. No extra search cluster is needed - it is simply another Couchbase
service running next to the Data and Query services.

Run it on its own with:
    docker compose run --rm app python search_demo.py
"""
import time

from couchbase.options import SearchOptions
from couchbase.search import (
    BooleanQuery, ConjunctionQuery, DisjunctionQuery, HighlightStyle,
    MatchPhraseQuery, MatchQuery, SearchRequest, TermFacet, TermQuery,
)

from common import (
    CB_BUCKET, SCOPE, banner, connect, console, keyspace, note, run_query,
    section, show_table, step, why,
)

INDEX = "hotel_text"   # created by init/setup.sh from init/search/hotel_text.json


def search(scope, query, label, limit=5, show_fields=("name", "city", "country"), **options):
    """Run one Search query, print the hits with their relevance scores and timings.

    Returns the result so callers can read facets or other extras.
    """
    start = time.perf_counter()
    result = scope.search(
        INDEX,
        SearchRequest.create(query),
        SearchOptions(limit=limit, fields=list(show_fields), **options),
    )
    # The SDK streams results; reading them here is what actually fetches them.
    rows = list(result)
    round_trip_ms = (time.perf_counter() - start) * 1000
    total = result.metadata().metrics().total_rows()

    table = []
    for row in rows:
        entry = {"score": round(row.score, 3)}
        entry.update({f: row.fields.get(f) for f in show_fields})
        table.append(entry)
    show_table(table, max_rows=limit)

    if options.get("highlight_style"):
        # Fragments are the snippets of text that matched, with each matching
        # word wrapped in <mark>...</mark>. Here we show them in bold.
        console.print("  [bold]Why each hotel matched (highlighted text):[/bold]")
        for row in rows:
            snippet = " … ".join(sum((row.fragments or {}).values(), []))
            snippet = snippet.replace("<mark>", "[bold yellow]").replace("</mark>", "[/bold yellow]")
            console.print(f"    • {row.fields.get('name')}: {snippet}")
    console.print(
        f"  [green]{total} total hit(s)[/green] (showing top {len(rows)})  "
        f"⏱  round trip [bold green]{round_trip_ms:.1f} ms[/bold green]"
    )
    return result


def main():
    banner("Demo 2 · Full Text Search",
           "Language-aware search over hotel descriptions  ·  Search index: " + INDEX)
    cluster = connect()
    # Our index lives inside the 'inventory' scope, so we search through the scope.
    scope = cluster.bucket(CB_BUCKET).scope(SCOPE)

    section("Part 1 · Searching text")
    note("Higher score = more relevant. Scores come from word frequency and rarity (TF-IDF).")

    # ------------------------------------------------------------------ MATCH
    step('1. Basic match - hotel descriptions mentioning "beach"')
    console.print('  [dim]MatchQuery("beach", field="description")[/dim]')
    search(scope, MatchQuery("beach", field="description"), "match")
    why("A match query analyzes your words (lower-casing, stemming) and ranks by relevance - "
        "so 'beaches' and 'Beach' also match, and the best matches come first.")

    # ----------------------------------------------------------------- PHRASE
    step('2. Phrase match - the exact phrase "continental breakfast"')
    console.print('  [dim]MatchPhraseQuery("continental breakfast", field="description")[/dim]')
    search(scope, MatchPhraseQuery("continental breakfast", field="description"), "phrase")
    why("A phrase query requires the words to appear together and in order, "
        "so it is far more precise than matching each word separately.")

    # ------------------------------------------------------------------ FUZZY
    step('3. Fuzzy match - a deliberate typo: "beech" instead of "beach"')
    console.print('  [dim]MatchQuery("beech", field="description", fuzziness=1)[/dim]')
    search(scope, MatchQuery("beech", field="description", fuzziness=1), "fuzzy")
    why("Fuzziness tolerates typos (here, one wrong letter), so people still find what "
        "they meant. Notice the scores are lower than an exact match - Couchbase ranks "
        "exact hits above approximate ones.")

    # --------------------------------------------------------------- COMPOUND
    section("Part 2 · Combining conditions, facets and highlighting")

    step("4. Compound query - must / should / must_not, plus a field filter")
    console.print(
        '  [dim]must:     description matches "breakfast"  AND  country is "United States"\n'
        '  should:   description matches "pool"     (nice to have - boosts the score)\n'
        '  must_not: description matches "motel"    (exclude these)[/dim]'
    )
    compound = BooleanQuery(
        must=ConjunctionQuery(
            MatchQuery("breakfast", field="description"),
            TermQuery("United States", field="country"),   # exact value filter
        ),
        should=DisjunctionQuery(MatchQuery("pool", field="description")),
        must_not=DisjunctionQuery(MatchQuery("motel", field="description")),
    )
    search(scope, compound, "compound")
    why("Real searches mix text relevance with hard filters. must = required, "
        "should = boosts the ranking, must_not = excluded - all in one request to one service.")

    # --------------------------------------------------- FACETS + HIGHLIGHTING
    step('5. Facets and highlighting - "beach" hotels, counted by country and city')
    console.print('  [dim]facets: TermFacet("country"), TermFacet("city")   highlight: description[/dim]')
    result = search(
        scope,
        MatchQuery("beach", field="description"),
        "facets",
        limit=3,
        facets={"countries": TermFacet("country", 5), "cities": TermFacet("city", 5)},
        highlight_style=HighlightStyle.Html,
        highlight_fields=["description"],
    )
    for name, facet in result.facets().items():
        counts = ", ".join(f"{t.term} ({t.count})" for t in facet.terms)
        console.print(f"  [bold]{name}[/bold]: {counts}")
    why("Facets give you the 'filter by country / city (count)' sidebar you see on shopping and "
        "travel sites, computed in the same request. Highlighting shows WHY each result matched.")

    # ------------------------------------------------------------ SEARCH + SQL++
    section("Part 3 · Search and SQL++ together")
    step("6. The SEARCH() function - full-text relevance inside a SQL++ statement")
    # NOTE: inside SEARCH() the index name must be fully qualified: bucket.scope.index
    sql = f"""
SELECT h.name, h.city, h.pets_ok, SEARCH_SCORE() AS score
FROM {keyspace('hotel')} AS h
WHERE SEARCH(h, {{"query": {{"match": "beach", "field": "description"}}}},
             {{"index": "{CB_BUCKET}.{SCOPE}.{INDEX}"}})
  AND h.country = "United States"
  AND h.pets_ok = true
ORDER BY score DESC
LIMIT 5"""
    run_query(cluster, sql, columns=["name", "city", "pets_ok", "score"])
    why("One statement combines language-aware relevance (SEARCH) with ordinary SQL filters "
        "(country, pets_ok) and sorting - and you can still JOIN or aggregate the result.")

    console.print("\n[bold green]✔ Demo 2 complete.[/bold green]")


if __name__ == "__main__":
    main()
