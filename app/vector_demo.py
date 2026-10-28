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

Each step is the SDK call you would write in your own application; `from_search` and
`from_query` only format the result for display.

    docker compose run --rm app python vector_demo.py
"""

from couchbase.options import QueryOptions, SearchOptions
from couchbase.search import (
    BooleanFieldQuery,
    ConjunctionQuery,
    MatchQuery,
    SearchRequest,
    TermQuery,
)
from couchbase.vector_search import VectorQuery, VectorSearch

from cli import run
from config import TEXT_INDEX, VECTOR_INDEX
from display import request_json
from embeddings import embed_text
from framework import Context, Demo, Result
from results import from_query, from_search, search_table
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
    "semantic search runs on operational data with no second database to keep in sync. "
    "The score is cosine similarity: 1.0 means identical meaning.",
)
def nearest(ctx: Context) -> Result:
    fields = ["name", "city", "country"]
    query = VectorQuery("embedding", embed_text(QUESTION), num_candidates=5)
    request = SearchRequest.create(VectorSearch.from_vector_query(query))
    result = ctx.scope.search(VECTOR_INDEX, request, SearchOptions(limit=5, fields=fields))
    return from_search(request_json(query), result, fields=fields, score_label="similarity")


@DEMO.step(
    "keyword-vs-vector",
    "The same question as a keyword search and as a vector search",
    section=COMPARE,
    why="Keyword search only sees words, so it returns hotels that merely contain 'family' or "
    "'quiet'. Vector search ranks by intent. This is a small, fast model over short descriptions, "
    "so some results are only loosely related; larger hosted models are sharper.",
)
def keyword_vs_vector(ctx: Context) -> Result:
    fields = ["name", "description"]
    options = SearchOptions(limit=5, fields=fields)
    vector_query = VectorQuery("embedding", embed_text(QUESTION), num_candidates=5)

    with Stopwatch() as keyword_time:
        keyword = ctx.scope.search(
            TEXT_INDEX, SearchRequest.create(MatchQuery(QUESTION, field="description")), options
        )
        keyword_table = search_table(keyword, fields, title="Keyword search")
    with Stopwatch() as vector_time:
        vector = ctx.scope.search(
            VECTOR_INDEX,
            SearchRequest.create(VectorSearch.from_vector_query(vector_query)),
            options,
        )
        vector_table = search_table(vector, fields, title="Vector search", score_label="similarity")

    return Result(
        tables=[keyword_table, vector_table],
        side_by_side=True,
        timings={"keyword search": keyword_time.ms, "vector search": vector_time.ms},
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
    fields = ["name", "city", "pets_ok"]
    prefilter = ConjunctionQuery(
        TermQuery("United States", field="country"),
        BooleanFieldQuery(True, field="pets_ok"),
    )
    query = VectorQuery("embedding", embed_text(QUESTION), num_candidates=5, prefilter=prefilter)
    request = SearchRequest.create(VectorSearch.from_vector_query(query))
    result = ctx.scope.search(VECTOR_INDEX, request, SearchOptions(limit=5, fields=fields))
    return from_search(request_json(query), result, fields=fields, score_label="similarity")


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
    statement = f"""
        SELECT h.name, h.city,
               ROUND(1 - APPROX_VECTOR_DISTANCE(h.embedding, $qvec, "COSINE"), 3) AS similarity
        FROM {ctx.keyspace("hotel")} AS h USE INDEX (idx_hotel_composite USING GSI)
        WHERE h.country = "United States" AND h.pets_ok = true
        ORDER BY APPROX_VECTOR_DISTANCE(h.embedding, $qvec, "COSINE")
        LIMIT 5"""
    vector = embed_text(QUESTION)
    options = QueryOptions(named_parameters={"qvec": vector}, metrics=True)
    result = ctx.cluster.query(statement, options)
    return from_query(
        statement,
        result,
        parameters={"qvec": f"[{len(vector)}-number query vector]"},
        columns=["name", "city", "similarity"],
    )


@DEMO.step(
    "sqlpp-hyperscale",
    "The same query on a Hyperscale vector index (built for very large vector sets)",
    section=HYBRID,
    why="Hyperscale indexes are designed for millions to billions of vectors. Same SQL++ and "
    "same results here; the choice between the two matters at scale.",
)
def sqlpp_hyperscale(ctx: Context) -> Result:
    statement = f"""
        SELECT h.name, h.city,
               ROUND(1 - APPROX_VECTOR_DISTANCE(h.embedding, $qvec, "COSINE"), 3) AS similarity
        FROM {ctx.keyspace("hotel")} AS h USE INDEX (idx_hotel_hyperscale USING GSI)
        WHERE h.country = "United States" AND h.pets_ok = true
        ORDER BY APPROX_VECTOR_DISTANCE(h.embedding, $qvec, "COSINE")
        LIMIT 5"""
    vector = embed_text(QUESTION)
    options = QueryOptions(named_parameters={"qvec": vector}, metrics=True)
    result = ctx.cluster.query(statement, options)
    return from_query(
        statement,
        result,
        parameters={"qvec": f"[{len(vector)}-number query vector]"},
        columns=["name", "city", "similarity"],
    )


if __name__ == "__main__":
    raise SystemExit(run([DEMO]))
