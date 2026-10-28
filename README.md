# Try Couchbase in 3 commands

A zero-setup demo of Couchbase Server. With only Docker installed you get a running cluster, a real sample data set, and a guided demo you can use in your **browser** or your **terminal**, covering:

| Demo | What you will see |
|---|---|
| **1. Key-Value & SQL++** | Sub-millisecond document reads and writes with the Python SDK, then SQL++ (SQL for JSON): filters, parameters, GROUP BY, JOIN, UNNEST |
| **2. Full Text Search** | Language-aware search: match, phrase, typo-tolerant (fuzzy), compound queries, facets, highlighting, and `SEARCH()` inside SQL++ |
| **3. Vector search** | Semantic search by *meaning*: a keyword vs. vector comparison, filtered (hybrid) vector search, and vector queries in SQL++ |

Everything runs in containers, so it behaves the same on **macOS (Intel and Apple Silicon), Windows and Linux**. Nothing is installed on your machine, and no accounts or API keys are needed.

---

## Quick start

**Prerequisites:** [Docker Desktop](https://www.docker.com/products/docker-desktop/) (or Docker Engine with the Compose plugin), with **at least 6 GB of memory available to Docker, 8 GB recommended** (see [Memory](#memory-and-disk)).

```bash
# 1. Get the demo
git clone https://github.com/<org>/couchbase-docker-travel-sample-basics.git
cd couchbase-docker-travel-sample-basics

# 2. Start Couchbase, load the data, build the indexes, create embeddings, start the web UI
docker compose up -d --wait

# 3. Open the guided demo in your browser
#    http://localhost:8501
```

Step 2 takes a few minutes the first time, mostly downloading about 4 GB of images (in our test on a fast connection the whole step took about 90 seconds; allow 5–10 minutes on a slower one). It also loads about 60,000 documents and builds the indexes. It prints `Healthy` for each service when everything is ready.

### Two ways to use it

Use either one, or both at the same time. They run the same steps against the same cluster.

**In your browser: <http://localhost:8501>**
- **Guided tour:** pick a demo, then press **▶ Run** on any step (or **Run all steps**). Each step shows the SQL++ or SDK call, the results, and the timings. Switch on **Show code** to see the exact Python that runs, and open **Run this step in a terminal** for the equivalent command.
- **Playground:** type a question in your own words, optionally add filters (country, pets, breakfast), and see **keyword search and vector search side by side**.

**In your terminal:**

```bash
docker compose run --rm app
```

This walks you through the demos one step at a time. After each step it pauses so you can read it: **press Enter to continue, `a` to run the rest without pausing, or `q` to quit**. Each step shows the SQL++ or SDK call next to its results, row counts, timings, and a one-line "why this matters". If there is no keyboard attached (for example in CI) it runs straight through.

### Run one demo at a time

```bash
docker compose run --rm app python kv_and_queries.py   # Demo 1: Key-Value and SQL++
docker compose run --rm app python search_demo.py      # Demo 2: Full Text Search
docker compose run --rm app python vector_demo.py      # Demo 3: Vector search
```

Options (they work with every demo, and with `docker compose run --rm app`):

| Option | What it does |
|---|---|
| `--show-code` | Print the Python source of each step before it runs, so you see exactly what executes |
| `--list` | List the step names |
| `--only STEP` | Run just that step (repeatable), e.g. `--only kv-get --only sql-join` |
| `--no-pause` | Do not wait for Enter between steps |
| `--rows N` | Rows shown per table (default 5) |

The demo source is in [`app/`](app/) and is written as teaching material: each step is one short function that calls Couchbase and returns what it found. Open the files, change a query, and run it again. Your edits take effect immediately, with no rebuild.

### Go deeper

For when you want numbers, plans and internals rather than a walkthrough:

```bash
# Which indexes does a query use?  (shows the query plan)
docker compose run --rm app python deepdive.py explain 'SELECT name FROM `travel-sample`.inventory.airline WHERE country = "France"'

# Latency percentiles (p50 / p95 / p99) for KV, SQL++, Search and vector operations
docker compose run --rm app python deepdive.py bench --runs 200

# How close are the approximate vector indexes to the exact answer?
docker compose run --rm app python deepdive.py recall --k 10

# An interactive SQL++ shell
docker compose exec couchbase cbq -u Administrator -p password123
```

`bench` states its conditions (single node, one client thread, same machine): it shows the cost of one round trip, not production throughput. `recall` compares each vector index with a brute-force search over every embedding.

---

## Explore the data yourself

Couchbase has a full web console. After step 2, open **<http://localhost:8091>** and sign in:

| | |
|---|---|
| **Username** | `Administrator` |
| **Password** | `password123` |

(These are demo-only credentials and live in [`.env`](.env).)

**Query Workbench** (left menu → *Query*). Pick the `travel-sample` bucket and the `inventory` scope, then try:

```sql
SELECT name, city, country FROM hotel WHERE country = "France" LIMIT 10;
```

**Search** (left menu → *Search*). You will find two indexes, `hotel_text` (keywords) and `hotel_vector` (meaning). Open one, choose *Search*, and try the query `beach`. Both are scoped to `travel-sample` → `inventory`.

**Documents** (left menu → *Documents*). Browse `inventory` → `hotel`, open any hotel, and look for the `embedding` field the vector demo added.

---

## What `docker compose up` does

```
couchbase   Couchbase Server 8.0 (Enterprise Edition) - the database
   │
init        configures the cluster, loads travel-sample, creates a limited-privilege
   │        app user, builds the SQL++ (GSI) indexes and the Full Text Search index
   │
embed       creates a vector embedding for every hotel (locally, no API key) and builds
   │        the vector indexes (Search, Hyperscale and Composite)
   │
web         the guided web UI at http://localhost:8501
app         the command-line demos - run on demand with `docker compose run --rm app`
```

- **`init` and `embed` show as "running" afterwards, but they are idle.** They stay up deliberately: that is how `docker compose up --wait` knows setup finished successfully (and fails with an error if it did not). They use about 5 MB of memory each.
- **The web UI is published on your machine only** (`127.0.0.1`), not on your network, and it sends no usage telemetry.
- **Security / least privilege:** the demos never log in as the administrator. They use a user called `demo` whose roles are limited to the `travel-sample` bucket (`data_reader`, `data_writer`, `query_select`, `fts_searcher`). See [`init/setup.sh`](init/setup.sh).
- **Embeddings:** all 917 hotels are embedded using the open-source model `all-MiniLM-L6-v2` (384 dimensions), run locally by [fastembed](https://github.com/qdrant/fastembed). The model is built into the Docker image, so no download happens at demo time. Embedding takes under a minute. The vectors are stored in each hotel document in a field named `embedding`.

### Repository layout

```
docker-compose.yml     the whole stack
.env                   versions, ports, credentials, memory quotas
init/setup.sh          cluster setup (idempotent, safe to re-run)
init/indexes.n1ql      SQL++ index definitions
init/search/*.json     Full Text Search index definition
init/vector/           vector index definitions
app/                   the demos (Python, Couchbase SDK 4.x)
  kv_and_queries.py      Demo 1: one function per step
  search_demo.py         Demo 2
  vector_demo.py         Demo 3
  deepdive.py            explain / bench / recall
  web_ui.py              the web UI (Streamlit); playground.py is the logic behind its playground
  catalog.py             the list of demos, shared by the CLI and the web UI
  embeddings.py          the one place text becomes a vector (swap in OpenAI, Bedrock, ...)
  embed.py               one-time step: embeddings and vector indexes
  framework.py, terminal.py, cli.py, client.py, config.py, timing.py   shared plumbing
  tests/                 unit tests (no cluster needed)
  integration/           drives the web UI headlessly against a live cluster
docs/run-locally.md    run the demos against any cluster, e.g. Couchbase Capella
```

---

## Memory and disk

Couchbase runs several services (Data, Index, Query, Search) in one container, so it needs more memory than a typical database container.

| | |
|---|---|
| Minimum Docker memory | **6 GB** |
| Recommended | **8 GB** (Docker Desktop → Settings → Resources → Memory) |
| Disk | about **9 GB** free (images ≈ 4.1 GB, plus data) |
| Memory in use once running | about 2.5 GB (Couchbase ≈ 1.9 GB, web UI ≈ 0.25 GB) |

The service memory quotas (Data 1 GB, Index 768 MB, Search 768 MB) are set in [`.env`](.env) and add up to about 2.5 GB, leaving headroom for the Query service and the operating system.

---

## Reset or stop

```bash
docker compose down        # stop (keeps your data; the next `up` is fast)
docker compose down -v     # delete everything, including data, for a fresh start
```

After `down -v`, `docker compose up -d --wait` rebuilds the whole environment from scratch.

---

## Troubleshooting

**`port is already allocated` / the container will not start.**
Something on your machine is using a Couchbase port (8091, 8093, 8094 or 11210). Either stop that program, or change the host ports at the bottom of [`.env`](.env), for example `CB_PORT_UI=18091`. The demos use the internal network, so they keep working.

**The web UI does not open at http://localhost:8501.**
Run `docker compose ps` and check that `web` is `healthy`, then `docker compose logs web`. If port 8501 is taken by something else, set `CB_PORT_WEB` in [`.env`](.env) (for example `CB_PORT_WEB=8502`) and run `docker compose up -d --wait` again. Results appear a moment after you press a button, because each step really runs against the cluster.

**`up --wait` fails, or services restart or are killed (exit code 137).**
Almost always low memory. Raise Docker Desktop's memory to 8 GB (Settings → Resources), then run `docker compose down -v` and start again. Check with `docker stats`.

**The first `up` is slow.**
It downloads about 4 GB and loads about 60,000 documents, so it takes several minutes on a first run. Later runs take well under a minute. Watch progress with:

```bash
docker compose logs -f init embed
```

**The embedding step seems stuck.**
It normally finishes in under a minute. Check `docker compose logs embed`. On a very low-memory machine it can take longer. Increase Docker's memory and run `docker compose up -d --wait` again; it resumes where it left off (hotels that already have an embedding are skipped).

**A demo says it cannot connect.**
Run `docker compose ps` and make sure `couchbase`, `init` and `embed` show `healthy`. If they do not, re-run `docker compose up -d --wait` and read the error.

**Windows: `bad interpreter` or `\r` errors.**
Git converted line endings. This repository includes a `.gitattributes` that prevents it. If you still see it, run `git config core.autocrlf false`, delete the folder, and clone again.

**Apple Silicon or ARM Linux.**
Supported. Docker pulls the native ARM images automatically.

**Start over completely.**
`docker compose down -v` followed by `docker compose up -d --wait`.

---

## Using your own cluster (including Couchbase Capella)

The demo files can also run against any Couchbase cluster, for example a free [Couchbase Capella](https://www.couchbase.com/products/capella/) trial. See [docs/run-locally.md](docs/run-locally.md).

## Versions

Pinned in [`.env`](.env): Couchbase Server `enterprise-8.0.3`, Couchbase Python SDK `4.6.4`, fastembed `0.9.1`, Python `3.12`.

Couchbase Server Enterprise Edition is free to use for development and evaluation. See the [Couchbase licensing information](https://www.couchbase.com/) for production use.
