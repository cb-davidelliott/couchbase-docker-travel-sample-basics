"""One-time embedding step: give every hotel a "meaning fingerprint" (a vector).

travel-sample has no vectors, so this step creates them. It runs automatically as part
of `docker compose up`, after the data is loaded. It:

  1. Builds a short text for each hotel (name, place, description, review excerpts).
  2. Turns that text into an EMBEDDING, a list of 384 numbers, with a small open-source
     model that runs locally in this container. Similar meanings get similar numbers.
  3. Saves the embedding in the hotel document as a new field, `embedding`. Couchbase
     stores it next to the rest of the hotel's data: there is no separate vector
     database to keep in sync.
  4. Creates the vector indexes and waits until they are ready:
       - a Search (FTS) vector index  -> init/vector/hotel_vector.json
       - GSI vector indexes (Hyperscale and Composite)  -> init/vector/vector_indexes.n1ql

It is safe to run again: hotels that already have an embedding are skipped, and indexes
that already exist are left alone. All ~917 hotels are embedded.
"""

import os
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import couchbase.subdocument as SD
import requests
from couchbase.cluster import Cluster
from rich.console import Console

from client import connect
from config import TEXT_INDEX, Settings, get_settings, require_env
from embeddings import embed_texts

BATCH_SIZE = 64
WRITER_THREADS = 8
HTTP_TIMEOUT = 30
VECTOR_INDEXES = ("idx_hotel_hyperscale", "idx_hotel_composite")

INIT_DIR = Path(os.environ.get("INIT_DIR", "/init"))  # where the index definitions live
console = Console(highlight=False)


def log(message: str) -> None:
    console.print(f"  {message}")


def hotel_text(hotel: dict[str, Any]) -> str:
    """The text to embed for one hotel, kept to ~1000 characters so it fits the model's input."""
    parts = [
        f"{hotel.get('name', '')}. {hotel.get('city') or ''}, {hotel.get('country') or ''}.",
        (hotel.get("description") or "").strip(),
    ]
    # Add excerpts from the first two reviews: they describe the real guest experience
    # (location, noise, family-friendliness) far better than a short description does.
    parts += [(review.get("content") or "")[:300] for review in (hotel.get("reviews") or [])[:2]]
    return " ".join(p for p in parts if p)[:1000]


def wait_until(what: str, timeout_s: int, ready: Callable[[], bool]) -> None:
    """Poll `ready()` every few seconds until it returns True, or exit after the timeout."""
    waited = 0
    while True:
        try:
            if ready():
                return
        except Exception:  # the service may still be starting; keep polling
            pass
        if waited >= timeout_s:
            raise SystemExit(f"Timed out after {timeout_s}s waiting for {what}.")
        time.sleep(3)
        waited += 3
        if waited % 15 == 0:
            log(f"[dim]still waiting for {what} ({waited}s)...[/dim]")


def embed_hotels(cluster: Cluster, settings: Settings) -> int:
    """Add an `embedding` field to every hotel that lacks one. Returns the hotel count."""
    console.print("\n[bold][1/4] Generating embeddings for the hotels[/bold]")
    hotels = settings.keyspace("hotel")
    total = next(iter(cluster.query(f"SELECT RAW COUNT(*) FROM {hotels}")))
    todo = list(
        cluster.query(
            f"SELECT META(h).id AS id, h.name, h.city, h.country, h.description, h.reviews "
            f"FROM {hotels} AS h WHERE h.embedding IS MISSING"
        )
    )
    log(f"{total} hotels in total, {total - len(todo)} already embedded, {len(todo)} to do.")
    if not todo:
        log("[green]✔ Nothing to do.[/green]")
        return total

    log(f"Loading the local embedding model ({settings.embedding_model})...")
    embed_texts(["warm up"])  # loads the model once, so the timing below is accurate
    collection = cluster.bucket(settings.bucket).scope(settings.scope).collection("hotel")

    def save(item: tuple[str, list[float]]) -> None:
        doc_id, vector = item
        # Sub-document write: add only the `embedding` field; the rest of the document is untouched.
        collection.mutate_in(doc_id, [SD.upsert("embedding", vector)])

    start = time.perf_counter()
    for first in range(0, len(todo), BATCH_SIZE):
        batch = todo[first : first + BATCH_SIZE]
        vectors = embed_texts([hotel_text(h) for h in batch])
        with ThreadPoolExecutor(max_workers=WRITER_THREADS) as pool:
            list(pool.map(save, ((h["id"], v) for h, v in zip(batch, vectors, strict=True))))
        log(f"embedded {first + len(batch)}/{len(todo)} hotels")
    log(f"[green]✔ Embedded {len(todo)} hotels in {time.perf_counter() - start:.1f}s[/green]")
    return total


def create_search_vector_index(settings: Settings, admin: tuple[str, str], total: int) -> None:
    """Create the Search vector index from its JSON definition and wait until it is built."""
    console.print("\n[bold][2/4] Creating the Search vector index[/bold]")
    name = "hotel_vector"
    host = os.environ.get("CB_HOST", "couchbase")
    url = f"http://{host}:8094/api/bucket/{settings.bucket}/scope/{settings.scope}/index/{name}"
    if requests.get(url, auth=admin, timeout=HTTP_TIMEOUT).status_code == 200:
        log(f"Search index '{name}' already exists - skipping")
    else:
        response = requests.put(
            url,
            auth=admin,
            data=(INIT_DIR / "vector" / "hotel_vector.json").read_text(),
            headers={"Content-Type": "application/json"},
            timeout=HTTP_TIMEOUT,
        )
        response.raise_for_status()
        log(f"Created Search index '{name}' (field 'embedding', {settings.embedding_dims} dims)")

    def indexed() -> bool:
        reply = requests.get(f"{url}/count", auth=admin, timeout=HTTP_TIMEOUT)
        return reply.json().get("count", 0) >= total

    wait_until(f"Search index '{name}' to finish indexing", 300, indexed)
    log(f"[green]✔ Search vector index is ready ({total} hotels)[/green]")


def create_gsi_vector_indexes(cluster: Cluster, settings: Settings) -> None:
    """Create the Hyperscale and Composite vector indexes and wait until they are online."""
    console.print("\n[bold][3/4] Creating the GSI vector indexes (Hyperscale and Composite)[/bold]")
    statements = (INIT_DIR / "vector" / "vector_indexes.n1ql").read_text().splitlines()
    for statement in (s.strip() for s in statements):
        if statement and not statement.startswith("--"):
            cluster.query(statement).execute()
            log(f"ran: {statement[:80]}...")

    def online() -> bool:
        states = list(
            cluster.query(
                "SELECT RAW state FROM system:indexes WHERE bucket_id = $bucket AND name IN $names",
                bucket=settings.bucket,
                names=list(VECTOR_INDEXES),
            )
        )
        return len(states) == len(VECTOR_INDEXES) and all(s == "online" for s in states)

    wait_until("the vector indexes to come online", 300, online)
    log("[green]✔ Hyperscale and Composite vector indexes are online[/green]")


def wait_for_search_in_sqlpp(cluster: Cluster, settings: Settings) -> None:
    """Wait until SQL++ SEARCH() is actually served by the Search index.

    Right after indexes are created, the query service needs a few seconds to pick up the
    new index metadata. Until then SEARCH() still returns rows, but evaluates them without
    the index (no relevance scores, different ordering). Waiting here means the first demo
    never sees that.
    """
    console.print("\n[bold][4/4] Checking that SQL++ can use the Search index[/bold]")
    index = f"{settings.bucket}.{settings.scope}.{TEXT_INDEX}"
    statement = (
        f"SELECT SEARCH_SCORE() AS score FROM {settings.keyspace('hotel')} AS h "
        f'WHERE SEARCH(h, {{"query": {{"match": "beach", "field": "description"}}}}, '
        f'{{"index": "{index}"}}) LIMIT 1'
    )

    def served_by_index() -> bool:
        rows = list(cluster.query(statement))
        return bool(rows) and rows[0]["score"] is not None

    wait_until("SEARCH() to be served by the Search index", 180, served_by_index)
    log("[green]✔ SEARCH() is served by the Search index[/green]")


def main() -> None:
    console.print("[bold red]Preparing semantic search: embeddings and vector indexes[/bold red]")
    settings = get_settings()
    admin = (require_env("CB_ADMIN_USER"), require_env("CB_ADMIN_PASSWORD"))
    cluster = connect(settings, credentials=admin)
    total = embed_hotels(cluster, settings)
    create_search_vector_index(settings, admin, total)
    create_gsi_vector_indexes(cluster, settings)
    wait_for_search_in_sqlpp(cluster, settings)
    console.print("\n[bold green]✔ Vector search is ready.[/bold green]")


if __name__ == "__main__":
    main()
