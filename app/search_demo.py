"""Demo 2: Full Text Search.

Couchbase has a built-in Search service (Full Text Search, or FTS). It understands
language: it splits text into words, handles plurals and typos, ranks results by
relevance, highlights matches and counts results by category (facets).

Search works on an index that the setup step already built. Its definition is in
init/search/hotel_text.json: it says which hotel fields to index and how to analyze
each one. You query it with the same connection as everything else; it is simply
another Couchbase service running next to the Data and Query services.

Each step builds a query object and calls `scope.search(...)`: that is the code you
would write in your own application. `from_search` only formats the hits for display,
and `request_json` shows the JSON the SDK sends, generated from the same query object.

    docker compose run --rm app python search_demo.py
"""

from couchbase.options import QueryOptions, SearchOptions
from couchbase.search import (
    BooleanQuery,
    ConjunctionQuery,
    DisjunctionQuery,
    HighlightStyle,
    MatchPhraseQuery,
    MatchQuery,
    SearchRequest,
    TermFacet,
    TermQuery,
)

from cli import run
from config import TEXT_INDEX
from display import request_json
from framework import Context, Demo, Result
from results import from_query, from_search

TEXT = "Part 1 · Searching text"
COMBINING = "Part 2 · Combining conditions, facets and highlighting"
WITH_SQLPP = "Part 3 · Search and SQL++ together"

HIT_FIELDS = ["name", "city", "country"]  # stored fields to return with each hit

DEMO = Demo(
    title="Demo 2 · Full Text Search",
    script="search_demo.py",
    subtitle=f"Language-aware search over hotel descriptions · index: {TEXT_INDEX}",
)


@DEMO.step(
    "match",
    'Basic match: hotel descriptions mentioning "beach"',
    section=TEXT,
    why="A match query analyzes your words (lower-casing, stemming) and ranks by relevance, "
    "so 'beaches' and 'Beach' also match and the best matches come first.",
)
def match(ctx: Context) -> Result:
    query = MatchQuery("beach", field="description")
    # Our index lives in the "inventory" scope, so we search through the scope.
    result = ctx.scope.search(
        TEXT_INDEX, SearchRequest.create(query), SearchOptions(limit=5, fields=HIT_FIELDS)
    )
    return from_search(request_json(query), result, fields=HIT_FIELDS)


@DEMO.step(
    "phrase",
    'Phrase match: the exact phrase "continental breakfast"',
    section=TEXT,
    why="A phrase query needs the words together and in order, so it is much more precise "
    "than matching each word separately.",
)
def phrase(ctx: Context) -> Result:
    query = MatchPhraseQuery("continental breakfast", field="description")
    result = ctx.scope.search(
        TEXT_INDEX, SearchRequest.create(query), SearchOptions(limit=5, fields=HIT_FIELDS)
    )
    return from_search(request_json(query), result, fields=HIT_FIELDS)


@DEMO.step(
    "fuzzy",
    'Fuzzy match: a deliberate typo, "beech" instead of "beach"',
    section=TEXT,
    why="Fuzziness tolerates typos (here, one wrong letter) so people still find what they "
    "meant. The scores are lower than for an exact match: exact hits rank above approximate ones.",
)
def fuzzy(ctx: Context) -> Result:
    query = MatchQuery("beech", field="description", fuzziness=1)
    result = ctx.scope.search(
        TEXT_INDEX, SearchRequest.create(query), SearchOptions(limit=5, fields=HIT_FIELDS)
    )
    return from_search(request_json(query), result, fields=HIT_FIELDS)


@DEMO.step(
    "compound",
    "Compound query: must / should / must_not, plus a field filter",
    section=COMBINING,
    why="Real searches mix text relevance with hard filters: must is required, should boosts "
    "the ranking, must_not excludes. All of it runs in one request to one service.",
)
def compound(ctx: Context) -> Result:
    query = BooleanQuery(
        must=ConjunctionQuery(
            MatchQuery("breakfast", field="description"),
            TermQuery("United States", field="country"),  # exact-value filter
        ),
        should=DisjunctionQuery(MatchQuery("pool", field="description")),  # boosts the score
        must_not=DisjunctionQuery(MatchQuery("motel", field="description")),  # excludes
    )
    result = ctx.scope.search(
        TEXT_INDEX, SearchRequest.create(query), SearchOptions(limit=5, fields=HIT_FIELDS)
    )
    return from_search(request_json(query), result, fields=HIT_FIELDS)


@DEMO.step(
    "facets",
    'Facets and highlighting: "beach" hotels counted by country and city',
    section=COMBINING,
    why="Facets give you the 'filter by country (count)' sidebar found on travel sites, computed "
    "in the same request. Highlighting shows why each result matched.",
)
def facets_and_highlighting(ctx: Context) -> Result:
    query = MatchQuery("beach", field="description")
    facets = {"countries": TermFacet("country", 5), "cities": TermFacet("city", 5)}
    highlight = ["description"]
    result = ctx.scope.search(
        TEXT_INDEX,
        SearchRequest.create(query),
        SearchOptions(
            limit=3,
            fields=HIT_FIELDS,
            facets=facets,
            highlight_style=HighlightStyle.Html,
            highlight_fields=highlight,
        ),
    )
    request = request_json(query, facets=facets, highlight_fields=highlight)
    return from_search(request, result, fields=HIT_FIELDS)


@DEMO.step(
    "search-in-sqlpp",
    "The SEARCH() function: full-text relevance inside SQL++",
    section=WITH_SQLPP,
    why="One statement combines language-aware relevance (SEARCH) with ordinary SQL filters and "
    "sorting, and you can still JOIN or aggregate the result.",
)
def search_in_sqlpp(ctx: Context) -> Result:
    # Inside SEARCH() the index name must be fully qualified: bucket.scope.index
    index = f"{ctx.settings.bucket}.{ctx.settings.scope}.{TEXT_INDEX}"
    statement = f"""
        SELECT h.name, h.city, h.pets_ok, SEARCH_SCORE() AS score
        FROM {ctx.keyspace("hotel")} AS h
        WHERE SEARCH(h, {{"query": {{"match": "beach", "field": "description"}}}},
                     {{"index": "{index}"}})
          AND h.country = "United States"
          AND h.pets_ok = true
        ORDER BY score DESC
        LIMIT 5"""
    result = ctx.cluster.query(statement, QueryOptions(metrics=True))
    return from_query(statement, result, columns=["name", "city", "pets_ok", "score"])


if __name__ == "__main__":
    raise SystemExit(run([DEMO]))
