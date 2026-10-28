"""Keyword search vs. vector search over the hotels, with optional filters.

This is the logic behind the web UI's playground. It lives apart from the UI so that it
can be read, tested and reused on its own: ask one question, get both kinds of answer.
"""

from dataclasses import dataclass

from couchbase.options import SearchOptions
from couchbase.search import (
    BooleanFieldQuery,
    ConjunctionQuery,
    MatchQuery,
    SearchQuery,
    SearchRequest,
    TermQuery,
)
from couchbase.vector_search import VectorQuery, VectorSearch

from config import TEXT_INDEX, VECTOR_INDEX
from embeddings import embed_text
from framework import Context
from timing import Stopwatch

HOTEL_FIELDS = ("name", "city", "country", "description")


@dataclass(frozen=True)
class Filters:
    """Hard requirements, as opposed to the free-text question."""

    country: str | None = None
    pets_ok: bool = False
    free_breakfast: bool = False

    def query(self) -> SearchQuery | None:
        """The filters as one Search query, or None when no filter is set."""
        clauses: list[SearchQuery] = []
        if self.country:
            clauses.append(TermQuery(self.country, field="country"))
        if self.pets_ok:
            clauses.append(BooleanFieldQuery(True, field="pets_ok"))
        if self.free_breakfast:
            clauses.append(BooleanFieldQuery(True, field="free_breakfast"))
        if not clauses:
            return None
        return clauses[0] if len(clauses) == 1 else ConjunctionQuery(*clauses)


@dataclass(frozen=True)
class Hit:
    score: float  # relevance for keyword search; cosine similarity for vector search
    name: str
    city: str
    country: str
    description: str


@dataclass(frozen=True)
class Comparison:
    """The same question answered both ways, with timings in milliseconds."""

    keyword: list[Hit]
    keyword_total: int  # every hotel the keyword query matched, not just the top results
    vector: list[Hit]
    embed_ms: float  # time to turn the question into a vector, in this application
    keyword_ms: float
    vector_ms: float


def _hit(row) -> Hit:
    fields = {name: row.fields.get(name) or "" for name in HOTEL_FIELDS}
    return Hit(round(row.score, 3), **fields)


def countries(ctx: Context) -> list[str]:
    """The countries that have hotels, for the filter drop-down."""
    statement = (
        f"SELECT DISTINCT RAW country FROM {ctx.keyspace('hotel')} WHERE country IS NOT NULL"
    )
    return sorted(ctx.cluster.query(statement))


def compare(ctx: Context, text: str, filters: Filters, k: int = 5) -> Comparison:
    """Search the hotels for `text` with a keyword query and with a vector query."""
    options = SearchOptions(limit=k, fields=list(HOTEL_FIELDS))
    prefilter = filters.query()

    # Keyword search: match the question's words against the hotel descriptions.
    keyword_query: SearchQuery = MatchQuery(text, field="description")
    if prefilter:
        keyword_query = ConjunctionQuery(keyword_query, prefilter)
    with Stopwatch() as keyword_time:
        keyword = ctx.scope.search(TEXT_INDEX, SearchRequest.create(keyword_query), options)
        keyword_hits = [_hit(row) for row in keyword]

    # Vector search: embed the question, then find the nearest hotel embeddings. The filter
    # is applied BEFORE the nearest-neighbour search, so we still get k results that qualify.
    with Stopwatch() as embed_time:
        vector = embed_text(text)
    request = SearchRequest.create(
        VectorSearch.from_vector_query(
            VectorQuery("embedding", vector, num_candidates=k, prefilter=prefilter)
        )
    )
    with Stopwatch() as vector_time:
        vector_hits = [_hit(row) for row in ctx.scope.search(VECTOR_INDEX, request, options)]

    return Comparison(
        keyword=keyword_hits,
        keyword_total=keyword.metadata().metrics().total_rows(),
        vector=vector_hits,
        embed_ms=embed_time.ms,
        keyword_ms=keyword_time.ms,
        vector_ms=vector_time.ms,
    )
