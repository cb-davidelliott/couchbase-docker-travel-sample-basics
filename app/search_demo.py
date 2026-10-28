"""Demo 2: Full Text Search.

Couchbase has a built-in Search service (Full Text Search, or FTS). It understands
language: it splits text into words, handles plurals and typos, ranks results by
relevance, highlights matches and counts results by category (facets).

Search works on an index that the setup step already built. Its definition is in
init/search/hotel_text.json: it says which hotel fields to index and how to analyze
each one. You query it with the same connection as everything else; it is simply
another Couchbase service running next to the Data and Query services.

    docker compose run --rm app python search_demo.py
"""

from couchbase.options import SearchOptions
from couchbase.search import (
    BooleanQuery,
    ConjunctionQuery,
    DisjunctionQuery,
    HighlightStyle,
    MatchPhraseQuery,
    MatchQuery,
    SearchQuery,
    SearchRequest,
    TermFacet,
    TermQuery,
)

from cli import run
from config import TEXT_INDEX
from framework import Context, Demo, Result, Table
from timing import Stopwatch

TEXT = "Part 1 · Searching text"
COMBINING = "Part 2 · Combining conditions, facets and highlighting"
WITH_SQLPP = "Part 3 · Search and SQL++ together"

HIT_FIELDS = ("name", "city", "country")  # stored fields to return with each hit

DEMO = Demo(
    title="Demo 2 · Full Text Search",
    script="search_demo.py",
    subtitle=f"Language-aware search over hotel descriptions · index: {TEXT_INDEX}",
)


def hotel_search(
    ctx: Context,
    query: SearchQuery,
    call: str,
    *,
    limit: int = 5,
    facets: dict[str, TermFacet] | None = None,
    highlight: bool = False,
) -> Result:
    """Search the hotel index and package the hits, scores and timing.

    Shared by the steps below so each step can focus on the query it demonstrates.
    `call` is the text shown to the viewer.
    """
    options = {"limit": limit, "fields": list(HIT_FIELDS)}
    if facets:
        options["facets"] = facets
    if highlight:
        options.update(highlight_style=HighlightStyle.Html, highlight_fields=["description"])

    with Stopwatch() as sw:
        # Our index lives in the "inventory" scope, so we search through the scope.
        result = ctx.scope.search(TEXT_INDEX, SearchRequest.create(query), SearchOptions(**options))
        hits = list(result)  # results stream in as we read them
    total = result.metadata().metrics().total_rows()

    # The score is relevance: higher means a better match.
    rows = [{"score": round(h.score, 3), **{f: h.fields.get(f) for f in HIT_FIELDS}} for h in hits]
    lines = []
    if highlight:
        lines.append("Why each hotel matched:")
        for hit in hits:
            snippet = " … ".join(sum((hit.fragments or {}).values(), []))
            lines.append(f"• {hit.fields['name']}: {snippet}")
    for name, facet in (result.facets() if facets else {}).items():
        lines.append(f"{name}: " + ", ".join(f"{t.term} ({t.count})" for t in facet.terms))
    return Result(
        statement=call,
        language="python",
        tables=[Table(rows, total=total)],
        lines=lines,
        timings={"round trip": sw.ms},
    )


@DEMO.step(
    "match",
    'Basic match: hotel descriptions mentioning "beach"',
    section=TEXT,
    why="A match query analyzes your words (lower-casing, stemming) and ranks by relevance, "
    "so 'beaches' and 'Beach' also match and the best matches come first.",
)
def match(ctx: Context) -> Result:
    return hotel_search(
        ctx,
        MatchQuery("beach", field="description"),
        'MatchQuery("beach", field="description")',
    )


@DEMO.step(
    "phrase",
    'Phrase match: the exact phrase "continental breakfast"',
    section=TEXT,
    why="A phrase query needs the words together and in order, so it is much more precise "
    "than matching each word separately.",
)
def phrase(ctx: Context) -> Result:
    return hotel_search(
        ctx,
        MatchPhraseQuery("continental breakfast", field="description"),
        'MatchPhraseQuery("continental breakfast", field="description")',
    )


@DEMO.step(
    "fuzzy",
    'Fuzzy match: a deliberate typo, "beech" instead of "beach"',
    section=TEXT,
    why="Fuzziness tolerates typos (here, one wrong letter) so people still find what they "
    "meant. The scores are lower than for an exact match: exact hits rank above approximate ones.",
)
def fuzzy(ctx: Context) -> Result:
    return hotel_search(
        ctx,
        MatchQuery("beech", field="description", fuzziness=1),
        'MatchQuery("beech", field="description", fuzziness=1)',
    )


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
    call = (
        "BooleanQuery(\n"
        '    must=[MatchQuery("breakfast", field="description"),\n'
        '          TermQuery("United States", field="country")],\n'
        '    should=[MatchQuery("pool", field="description")],\n'
        '    must_not=[MatchQuery("motel", field="description")])'
    )
    return hotel_search(ctx, query, call)


@DEMO.step(
    "facets",
    'Facets and highlighting: "beach" hotels counted by country and city',
    section=COMBINING,
    why="Facets give you the 'filter by country (count)' sidebar found on travel sites, computed "
    "in the same request. Highlighting shows why each result matched.",
)
def facets_and_highlighting(ctx: Context) -> Result:
    return hotel_search(
        ctx,
        MatchQuery("beach", field="description"),
        'MatchQuery("beach", field="description")  + TermFacet("country"), TermFacet("city")',
        limit=3,
        facets={"countries": TermFacet("country", 5), "cities": TermFacet("city", 5)},
        highlight=True,
    )


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
    return ctx.sqlpp(
        f"""
        SELECT h.name, h.city, h.pets_ok, SEARCH_SCORE() AS score
        FROM {ctx.keyspace("hotel")} AS h
        WHERE SEARCH(h, {{"query": {{"match": "beach", "field": "description"}}}},
                     {{"index": "{index}"}})
          AND h.country = "United States"
          AND h.pets_ok = true
        ORDER BY score DESC
        LIMIT 5""",
        columns=["name", "city", "pets_ok", "score"],
    )


if __name__ == "__main__":
    raise SystemExit(run([DEMO]))
