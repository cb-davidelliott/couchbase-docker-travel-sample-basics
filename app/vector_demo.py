"""
Demo 3: Semantic (vector) search
================================

Keyword search finds documents that contain your WORDS.
Vector search finds documents that match your MEANING.

How it works, in four steps:
  1. An embedding model turns text into a vector (a list of numbers).
     Similar meanings -> nearby vectors.                       (see embeddings.py)
  2. Each hotel's vector was stored in the hotel document itself, in a field
     called `embedding`, by the embed step that ran during `docker compose up`.
  3. A vector index lets Couchbase find the nearest vectors quickly.
  4. At query time we embed the user's question with the SAME model and ask
     Couchbase for the closest hotels.

Couchbase gives you more than one vector index. This demo uses:
  * a Search (FTS) vector index  - best when combining vectors with text/filters
  * GSI vector indexes (Hyperscale and Composite) - queried with plain SQL++

Run it on its own with:
    docker compose run --rm app python vector_demo.py
"""
import os
import time

from couchbase.options import SearchOptions
from couchbase.search import (
    BooleanFieldQuery, ConjunctionQuery, MatchQuery, SearchRequest, TermQuery,
)
from couchbase.vector_search import VectorQuery, VectorSearch
from rich.table import Table

from common import (
    CB_BUCKET, SCOPE, banner, connect, console, keyspace, note, run_query,
    section, show_table, step, why,
)
from embeddings import MODEL_NAME, embed_text

VECTOR_INDEX = "hotel_vector"   # Search vector index (init/vector/hotel_vector.json)
TEXT_INDEX = "hotel_text"       # Keyword Search index from Demo 2
DIMS = int(os.environ.get("EMBEDDING_DIMS", "384"))

QUESTION = "quiet beachfront place good for families with young kids"


def vector_search(scope, query_vector, k=5, prefilter=None, fields=("name", "city", "country")):
    """Ask the Search service for the k hotels whose embedding is nearest to
    `query_vector`. An optional `prefilter` limits which hotels are considered."""
    vq = VectorQuery("embedding", query_vector, num_candidates=k, prefilter=prefilter)
    request = SearchRequest.create(VectorSearch.from_vector_query(vq))
    start = time.perf_counter()
    result = scope.search(VECTOR_INDEX, request, SearchOptions(limit=k, fields=list(fields)))
    rows = list(result)
    return rows, (time.perf_counter() - start) * 1000


def keyword_search(scope, text, k=5):
    """Classic keyword search for the same text, for comparison."""
    start = time.perf_counter()
    result = scope.search(
        TEXT_INDEX, SearchRequest.create(MatchQuery(text, field="description")),
        SearchOptions(limit=k, fields=["name", "city", "country", "description"]),
    )
    rows = list(result)
    return rows, result.metadata().metrics().total_rows(), (time.perf_counter() - start) * 1000


def print_hits(rows, ms):
    table = [{"similarity": round(r.score, 3), "name": r.fields.get("name"),
              "city": r.fields.get("city"), "country": r.fields.get("country")} for r in rows]
    show_table(table)
    console.print(f"  ⏱  vector search [bold green]{ms:.1f} ms[/bold green]")


def main():
    banner("Demo 3 · Semantic (vector) search",
           f"Embedding model: {MODEL_NAME} ({DIMS} dimensions)  ·  runs locally, no API key")
    cluster = connect()
    scope = cluster.bucket(CB_BUCKET).scope(SCOPE)

    # ------------------------------------------------------------------ PART 1
    section("Part 1 · Turning a question into a vector")
    step(f'Embed the query:  "{QUESTION}"')
    embed_text("warm up")   # the first call loads the model (about a second); not part of the timing
    start = time.perf_counter()
    query_vector = embed_text(QUESTION)
    ms = (time.perf_counter() - start) * 1000
    preview = ", ".join(f"{x:.3f}" for x in query_vector[:8])
    console.print(f"  vector ({len(query_vector)} numbers): [{preview}, ...]")
    console.print(f"  ⏱  embedding generated in the app: [bold green]{ms:.1f} ms[/bold green]")
    why("The query is embedded here, in the application, with the SAME model that embedded the "
        "hotels. In production you would call a hosted model (OpenAI, Amazon Bedrock, Cohere, ...) "
        "instead: only the one function embed_texts() in embeddings.py changes.")

    # ------------------------------------------------------------------ PART 2
    section("Part 2 · Searching by meaning")
    step("Nearest hotels to that question (Search vector index)")
    note("None of the words 'beachfront', 'quiet', 'families' or 'young kids' need to appear in the hotel text.")
    rows, ms = vector_search(scope, query_vector)
    print_hits(rows, ms)
    note("similarity: 1.0 = identical meaning (cosine similarity).")
    why("Couchbase stores each embedding in the hotel's own JSON document and indexes it, so "
        "semantic search runs on your operational data - there is no second database to sync.")

    # ------------------------------------------------------------------ PART 3
    section("Part 3 · Keyword search vs. vector search, side by side")
    questions = [
        QUESTION,
        "somewhere to unwind after a long day hiking in the mountains",
    ]
    for q in questions:
        step(f'"{q}"')
        q_vec = embed_text(q)
        kw_rows, kw_total, kw_ms = keyword_search(scope, q)
        vec_rows, vec_ms = vector_search(scope, q_vec, k=len(kw_rows) or 5,
                                         fields=("name", "city", "country", "description"))

        grid = Table(header_style="bold", box=None, padding=(0, 2), pad_edge=False)
        grid.add_column(f"Keyword search  ({kw_total} matches, {kw_ms:.0f} ms)", width=38)
        grid.add_column(f"Vector search  ({vec_ms:.0f} ms)", width=38)
        for kw, vec in zip(kw_rows, vec_rows):
            grid.add_row(_cell(kw), _cell(vec))
        console.print(grid)
    why("Keyword search only sees words, so it returns hotels that merely contain 'family' or "
        "'quiet' - a spa resort in Sonoma tops the first list. Vector search ranks by intent: "
        "the beach motel and the beach resort come first, and the hiking query returns campsites "
        "and lodges. This is a small, fast model over short descriptions, so it is not perfect "
        "(a few results are only loosely related); larger hosted models are sharper.")

    # ------------------------------------------------------------------ PART 4
    section("Part 4 · Hybrid search: meaning + filters")
    step("Vector search restricted to pet-friendly hotels in the United States (Search)")
    console.print('  [dim]prefilter: country = "United States"  AND  pets_ok = true[/dim]')
    prefilter = ConjunctionQuery(
        TermQuery("United States", field="country"),
        BooleanFieldQuery(True, field="pets_ok"),
    )
    rows, ms = vector_search(scope, query_vector, prefilter=prefilter,
                             fields=("name", "city", "country", "pets_ok"))
    show_table([{"similarity": round(r.score, 3), "name": r.fields.get("name"),
                 "city": r.fields.get("city"), "pets_ok": r.fields.get("pets_ok")} for r in rows])
    console.print(f"  ⏱  filtered vector search [bold green]{ms:.1f} ms[/bold green]")
    why("Real questions mix meaning ('quiet beachfront') with hard requirements ('pet-friendly', "
        "'in the US'). The filter is applied BEFORE the nearest-neighbour search, so you always get "
        "a full set of results that satisfy it.")

    step("The same idea in SQL++: vector distance + WHERE clause (GSI vector indexes)")
    note("Two GSI vector index types exist. We pin each with USE INDEX so you can compare them.")
    for index, blurb in (
        ("idx_hotel_composite", "Composite: vector + scalar fields in ONE index, filters applied together"),
        ("idx_hotel_hyperscale", "Hyperscale: built for billions of vectors; filter applied alongside the search"),
    ):
        console.print(f"\n  [bold]{blurb}[/bold]")
        run_query(cluster, f"""
SELECT h.name, h.city,
       ROUND(1 - APPROX_VECTOR_DISTANCE(h.embedding, $qvec, "COSINE"), 3) AS similarity
FROM {keyspace('hotel')} AS h USE INDEX ({index} USING GSI)
WHERE h.country = "United States" AND h.pets_ok = true
ORDER BY APPROX_VECTOR_DISTANCE(h.embedding, $qvec, "COSINE")
LIMIT 5""", params={"qvec": query_vector},
                  display_params={"qvec": f"[{len(query_vector)}-number query vector]"},
                  columns=["name", "city", "similarity"])
    why("Vector search is just another SQL++ function: ORDER BY APPROX_VECTOR_DISTANCE(...). "
        "You can combine it with joins, filters and aggregates using the SQL skills your team already has. "
        "(APPROX_VECTOR_DISTANCE returns a distance, so 1 - distance gives the same similarity as above.)")

    console.print("\n[bold green]✔ Demo 3 complete.[/bold green]")


def _cell(row) -> str:
    """One result cell: hotel name, location, and a short description snippet."""
    desc = (row.fields.get("description") or "").strip().replace("\n", " ")
    desc = desc if len(desc) <= 60 else desc[:57] + "..."
    return f"[bold]{row.fields.get('name')}[/bold] ({row.fields.get('city')})\n[dim]{desc}[/dim]"


if __name__ == "__main__":
    main()
