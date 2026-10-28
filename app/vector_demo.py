"""Demo 3: Semantic (vector) search.

Keyword search finds documents that contain your WORDS. Vector search finds documents
that match your MEANING. How it works:

  1. An embedding model turns text into a vector (a list of numbers). Similar meanings
     give nearby vectors.                                          (see embeddings.py)
  2. Each hotel's vector was stored in the hotel document itself, in a field called
     `embedding`, by the embed step that ran during `docker compose up`.
  3. A vector index lets Couchbase find the nearest vectors quickly.
  4. At query time we embed the question with the SAME model and ask Couchbase for the
     closest hotels.

Couchbase offers more than one vector index. This demo uses:
  * a Search (FTS) vector index: best when combining vectors with text and filters
  * GSI vector indexes (Composite and Hyperscale): queried with plain SQL++

    docker compose run --rm app python vector_demo.py
"""

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

from cli import run
from config import TEXT_INDEX, VECTOR_INDEX
from embeddings import embed_text
from framework import Context, Demo, Result, Table
from timing import Stopwatch

EMBED = "Part 1 · Turning a question into a vector"
SEMANTIC = "Part 2 · Searching by meaning"
COMPARE = "Part 3 · Keyword search vs. vector search"
HYBRID = "Part 4 · Hybrid search: meaning + filters"

QUESTION = "quiet beachfront place good for families with young kids"

DEMO = Demo(
    title="Demo 3 · Semantic (vector) search",
    script="vector_demo.py",
    subtitle="Local embedding model, no API key · Search and GSI vector indexes",
)


def nearest_hotels(
    ctx: Context,
    vector: list[float],
    *,
    k: int = 5,
    prefilter: SearchQuery | None = None,
    extra: tuple[str, ...] = (),
) -> tuple[list[dict], float]:
    """Ask the Search service for the k hotels nearest to `vector`.

    An optional `prefilter` (any Search query) restricts which hotels are considered
    BEFORE the nearest-neighbour search. Returns the rows and the round-trip time in ms.
    """
    query = VectorQuery("embedding", vector, num_candidates=k, prefilter=prefilter)
    request = SearchRequest.create(VectorSearch.from_vector_query(query))
    fields = ["name", "city", *extra]
    with Stopwatch() as sw:
        hits = list(ctx.scope.search(VECTOR_INDEX, request, SearchOptions(limit=k, fields=fields)))
    # For a vector query the score is the cosine similarity: 1.0 means identical meaning.
    rows = [{"similarity": round(h.score, 3), **{f: h.fields.get(f) for f in fields}} for h in hits]
    return rows, sw.ms


def hotel_label(name: str, city: str) -> str:
    return f"{name} ({city})"


@DEMO.step(
    "embed-query",
    f'Embed the question: "{QUESTION}"',
    section=EMBED,
    why="The question is embedded here, in the application, with the SAME model that embedded "
    "the hotels. In production you would call a hosted model (OpenAI, Amazon Bedrock, Cohere): "
    "only embed_texts() in embeddings.py changes.",
)
def embed_query(ctx: Context) -> Result:
    embed_text("warm up")  # the first call loads the model; keep that out of the timing
    with Stopwatch() as sw:
        vector = embed_text(QUESTION)
    preview = ", ".join(f"{x:.3f}" for x in vector[:8])
    return Result(
        lines=[f"vector ({len(vector)} numbers): [{preview}, ...]"],
        timings={"embedding generated in the app": sw.ms},
    )


@DEMO.step(
    "nearest",
    "Nearest hotels to the question (Search vector index)",
    section=SEMANTIC,
    why="Each embedding is stored in the hotel's own JSON document and indexed there, so "
    "semantic search runs on operational data with no second database to keep in sync.",
)
def nearest(ctx: Context) -> Result:
    rows, ms = nearest_hotels(ctx, embed_text(QUESTION), extra=("country",))
    return Result(
        statement='VectorQuery("embedding", <384-number query vector>, num_candidates=5)',
        language="python",
        tables=[Table(rows)],
        lines=["similarity: 1.0 = identical meaning (cosine similarity)"],
        timings={"vector search": ms},
    )


@DEMO.step(
    "keyword-vs-vector",
    "The same question as a keyword search and as a vector search",
    section=COMPARE,
    why="Keyword search only sees words, so it returns hotels that merely contain 'family' or "
    "'quiet'. Vector search ranks by intent. This is a small, fast model over short descriptions, "
    "so some results are only loosely related; larger hosted models are sharper.",
)
def keyword_vs_vector(ctx: Context) -> Result:
    with Stopwatch() as keyword_time:
        keyword = ctx.scope.search(
            TEXT_INDEX,
            SearchRequest.create(MatchQuery(QUESTION, field="description")),
            SearchOptions(limit=5, fields=["name", "city", "description"]),
        )
        keyword_hits = list(keyword)
    vector_rows, vector_ms = nearest_hotels(ctx, embed_text(QUESTION), extra=("description",))

    return Result(
        tables=[
            Table(
                [
                    {
                        "hotel": hotel_label(h.fields["name"], h.fields["city"]),
                        "description": h.fields.get("description"),
                    }
                    for h in keyword_hits
                ],
                title=f"Keyword search ({keyword_time.ms:.0f} ms)",
                total=keyword.metadata().metrics().total_rows(),
            ),
            Table(
                [
                    {
                        "hotel": hotel_label(r["name"], r["city"]),
                        "description": r["description"],
                    }
                    for r in vector_rows
                ],
                title=f"Vector search ({vector_ms:.0f} ms)",
            ),
        ],
        side_by_side=True,
    )


@DEMO.step(
    "filtered-vector",
    "Vector search limited to pet-friendly hotels in the United States (Search)",
    section=HYBRID,
    why="Real questions mix meaning ('quiet beachfront') with hard requirements ('pet-friendly', "
    "'in the US'). The filter is applied BEFORE the nearest-neighbour search, so you always get "
    "a full set of results that satisfy it.",
)
def filtered_vector(ctx: Context) -> Result:
    prefilter = ConjunctionQuery(
        TermQuery("United States", field="country"),
        BooleanFieldQuery(True, field="pets_ok"),
    )
    rows, ms = nearest_hotels(ctx, embed_text(QUESTION), prefilter=prefilter, extra=("pets_ok",))
    return Result(
        statement=(
            'VectorQuery("embedding", <query vector>, prefilter=ConjunctionQuery(\n'
            '    TermQuery("United States", field="country"),\n'
            '    BooleanFieldQuery(True, field="pets_ok")))'
        ),
        language="python",
        tables=[Table(rows)],
        timings={"filtered vector search": ms},
    )


def vector_sqlpp(ctx: Context, index: str) -> Result:
    """SQL++ vector query pinned to one GSI vector index, so the two can be compared."""
    vector = embed_text(QUESTION)
    return ctx.sqlpp(
        f"""
        SELECT h.name, h.city,
               ROUND(1 - APPROX_VECTOR_DISTANCE(h.embedding, $qvec, "COSINE"), 3) AS similarity
        FROM {ctx.keyspace("hotel")} AS h USE INDEX ({index} USING GSI)
        WHERE h.country = "United States" AND h.pets_ok = true
        ORDER BY APPROX_VECTOR_DISTANCE(h.embedding, $qvec, "COSINE")
        LIMIT 5""",
        {"qvec": vector},
        columns=["name", "city", "similarity"],
        display_parameters={"qvec": f"[{len(vector)}-number query vector]"},
    )


@DEMO.step(
    "sqlpp-composite",
    "The same idea in SQL++: a Composite vector index (vector + scalar fields in one index)",
    section=HYBRID,
    why="Vector search is just another SQL++ function: ORDER BY APPROX_VECTOR_DISTANCE(...). "
    "A Composite index holds the scalar fields next to the vector, so the filter and the "
    "similarity search are served together. (The function returns a distance; 1 - distance "
    "is the similarity shown above.)",
)
def sqlpp_composite(ctx: Context) -> Result:
    return vector_sqlpp(ctx, "idx_hotel_composite")


@DEMO.step(
    "sqlpp-hyperscale",
    "The same query on a Hyperscale vector index (built for very large vector sets)",
    section=HYBRID,
    why="Hyperscale indexes are designed for millions to billions of vectors. Same SQL++ and "
    "same results here; the choice between the two matters at scale.",
)
def sqlpp_hyperscale(ctx: Context) -> Result:
    return vector_sqlpp(ctx, "idx_hotel_hyperscale")


if __name__ == "__main__":
    raise SystemExit(run([DEMO]))
