"""
One-time embedding step: give every hotel a "meaning fingerprint" (a vector).
=============================================================================

travel-sample has no vectors, so this step creates them. It runs automatically
as part of `docker compose up`, after the data is loaded.

What it does:
  1. Builds a short piece of text for each hotel (name + place + description
     + a couple of review excerpts).
  2. Turns that text into an EMBEDDING - a list of 384 numbers - using a small
     open-source model that runs locally inside this container (no API key,
     no internet access needed). Texts with similar meaning get similar numbers.
  3. Saves the embedding back into the hotel document as a new field called
     `embedding`. Couchbase stores it next to the rest of the hotel's data -
     there is no separate vector database to keep in sync.
  4. Creates the vector indexes and waits until they are ready:
       - a Search (FTS) vector index        -> init/vector/hotel_vector.json
       - GSI vector indexes (Hyperscale and Composite) -> init/vector/vector_indexes.n1ql

It is safe to run again: hotels that already have an embedding are skipped,
and indexes that already exist are left alone. All ~917 hotels are embedded.
"""
import os
import time
from concurrent.futures import ThreadPoolExecutor

import couchbase.subdocument as SD
import requests

from common import CB_BUCKET, SCOPE, connect, console, keyspace
from embeddings import MODEL_NAME, embed_texts

ADMIN = os.environ["CB_ADMIN_USER"]
ADMIN_PW = os.environ["CB_ADMIN_PASSWORD"]
DIMS = int(os.environ["EMBEDDING_DIMS"])
SEARCH_URL = f"http://{os.environ.get('CB_HOST', 'couchbase')}:8094"
BATCH_SIZE = 64


def log(msg):
    console.print(f"  {msg}")


def hotel_text(h: dict) -> str:
    """The text we embed for one hotel. Kept short (~1000 characters) so it fits
    comfortably inside the embedding model's input limit (256 tokens)."""
    parts = [
        f"{h.get('name', '')}. {h.get('city') or ''}, {h.get('country') or ''}.",
        (h.get("description") or "").strip(),
    ]
    # Add excerpts from the first two reviews: they describe the real guest
    # experience (location, noise, family-friendliness...) far better than the
    # short marketing description does.
    for review in (h.get("reviews") or [])[:2]:
        parts.append((review.get("content") or "")[:300])
    return " ".join(p for p in parts if p)[:1000]


def wait_for(what, timeout, check):
    waited = 0
    while True:
        try:
            if check():
                return
        except Exception:  # noqa: BLE001 - keep polling until the timeout
            pass
        if waited >= timeout:
            raise SystemExit(f"Timed out after {timeout}s waiting for {what}.")
        time.sleep(3)
        waited += 3
        if waited % 15 == 0:
            log(f"[dim]still waiting for {what} ({waited}s)...[/dim]")


def embed_hotels(cluster):
    console.print("\n[bold][1/3] Generating embeddings for the hotels[/bold]")
    total = next(iter(cluster.query(f"SELECT RAW COUNT(*) FROM {keyspace('hotel')}")))
    todo = list(cluster.query(
        f"SELECT META().id AS id, h.name, h.city, h.country, h.description, h.reviews "
        f"FROM {keyspace('hotel')} AS h WHERE h.embedding IS MISSING"
    ))
    log(f"{total} hotels in total, {total - len(todo)} already embedded, {len(todo)} to do.")
    if not todo:
        log("[green]✔ Nothing to do.[/green]")
        return total

    log(f"Loading the local embedding model ({MODEL_NAME}, {DIMS} dimensions)...")
    embed_texts(["warm up"])   # loads the model once, so timings below are accurate
    collection = cluster.bucket(CB_BUCKET).scope(SCOPE).collection("hotel")

    def save(item):
        doc_id, vector = item
        # Sub-document write: add just the `embedding` field; the rest of the
        # hotel document is untouched.
        collection.mutate_in(doc_id, [SD.upsert("embedding", vector)])

    start = time.perf_counter()
    done = 0
    for i in range(0, len(todo), BATCH_SIZE):
        batch = todo[i:i + BATCH_SIZE]
        # embeddings.py is the single place that turns text into vectors.
        vectors = embed_texts([hotel_text(h) for h in batch])
        items = [(h["id"], v) for h, v in zip(batch, vectors)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(save, items))
        done += len(batch)
        log(f"embedded {done}/{len(todo)} hotels")
    log(f"[green]✔ Embedded {done} hotels in {time.perf_counter() - start:.1f}s[/green]")
    return total


def create_search_vector_index(total):
    console.print("\n[bold][2/3] Creating the Search vector index[/bold]")
    name = "hotel_vector"
    url = f"{SEARCH_URL}/api/bucket/{CB_BUCKET}/scope/{SCOPE}/index/{name}"
    auth = (ADMIN, ADMIN_PW)
    if requests.get(url, auth=auth).status_code == 200:
        log(f"Search index '{name}' already exists - skipping")
    else:
        with open("/init/vector/hotel_vector.json") as f:
            definition = f.read()
        r = requests.put(url, auth=auth, data=definition, headers={"Content-Type": "application/json"})
        r.raise_for_status()
        log(f"Created Search index '{name}' (field 'embedding', {DIMS} dims, cosine)")

    def indexed():
        return requests.get(f"{url}/count", auth=auth).json().get("count", 0) >= total
    wait_for(f"Search index '{name}' to finish indexing", 300, indexed)
    log(f"[green]✔ Search vector index is ready ({total} hotels)[/green]")


def create_gsi_vector_indexes(cluster):
    console.print("\n[bold][3/3] Creating the GSI vector indexes (Hyperscale and Composite)[/bold]")
    with open("/init/vector/vector_indexes.n1ql") as f:
        statements = [line.strip() for line in f if line.strip() and not line.startswith("--")]
    for stmt in statements:
        cluster.query(stmt).execute()
        log(f"ran: {stmt[:90]}...")

    def all_online():
        rows = list(cluster.query(
            "SELECT RAW state FROM system:indexes "
            f"WHERE bucket_id = '{CB_BUCKET}' AND name LIKE 'idx_hotel_%' AND "
            "(name = 'idx_hotel_hyperscale' OR name = 'idx_hotel_composite')"))
        return len(rows) == 2 and all(r == "online" for r in rows)
    wait_for("the vector indexes to come online", 300, all_online)
    log("[green]✔ Hyperscale and Composite vector indexes are online[/green]")


def main():
    console.print("[bold red]Preparing semantic search: embeddings and vector indexes[/bold red]")
    cluster = connect(ADMIN, ADMIN_PW)
    total = embed_hotels(cluster)
    create_search_vector_index(total)
    create_gsi_vector_indexes(cluster)
    console.print("\n[bold green]✔ Vector search is ready.[/bold green]")


if __name__ == "__main__":
    main()
