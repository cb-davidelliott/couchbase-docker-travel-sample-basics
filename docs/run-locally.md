# Run the demos against your own cluster

The demos in [`app/`](../app/) are ordinary Python scripts. They do not need Docker. You can run them from a virtual environment against **any** Couchbase cluster: a self-managed server, or a free [Couchbase Capella](https://www.couchbase.com/products/capella/) trial.

Everything they need to know about the cluster comes from environment variables.

## 1. Set up Python

You need Python 3.10 to 3.12 (the SDK publishes pre-built packages for these).

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install couchbase==4.6.4 fastembed==0.9.1 rich==14.2.0 requests==2.32.5
```

The first time you run the vector demo, `fastembed` downloads the embedding model (about 90 MB) once.

## 2. Point the demos at your cluster

| Variable | Meaning | Example |
|---|---|---|
| `CB_CONNSTR` | Connection string | `couchbases://cb.xxxxxxxx.cloud.couchbase.com` (Capella) or `couchbase://localhost` |
| `CB_USER` | Database user | your database access user |
| `CB_PASSWORD` | Its password | |
| `CB_BUCKET` | Bucket holding the sample data | `travel-sample` (default) |

```bash
export CB_CONNSTR="couchbases://cb.xxxxxxxx.cloud.couchbase.com"
export CB_USER="demo"
export CB_PASSWORD="your-password"
export EMBEDDING_MODEL="sentence-transformers/all-MiniLM-L6-v2"
export EMBEDDING_DIMS=384
```

(On Windows PowerShell use `$env:CB_CONNSTR = "..."`.)

Then run any demo from the `app/` folder:

```bash
cd app
python kv_and_queries.py
python search_demo.py
python vector_demo.py
```

## 3. What your cluster needs first

The Docker setup does some work for you in the `init` and `embed` steps. On your own cluster you need to do the equivalent **once** before the demos will work.

| Needed by | What to do | Where to look |
|---|---|---|
| All demos | Load the **travel-sample** bucket. In Capella: *Data Tools → Import → Sample data*. In self-managed Server: *Settings → Sample Buckets*. | |
| All demos | Services enabled: **Data, Query, Index, Search**. | |
| All demos | A database user with `data_reader`, `data_writer`, `query_select` and `fts_searcher` (Capella: *Read/Write* access to the bucket) | [`init/setup.sh`](../init/setup.sh) step 4 |
| Demo 1 (SQL++) | Create the extra indexes. Paste the statements into the Query Workbench | [`init/indexes.n1ql`](../init/indexes.n1ql) |
| Demo 2 (Search) | Create the Search index `hotel_text` on `travel-sample.inventory.hotel`. Use *Search → Add Index → Import* and paste the JSON | [`init/search/hotel_text.json`](../init/search/hotel_text.json) |
| Demo 3 (vector) | **Add embeddings** to the hotel documents, then create the vector indexes | below |

### Adding the embeddings and vector indexes

The simplest way is to reuse the same step the Docker setup runs. [`app/embed.py`](../app/embed.py) uses administrator-level access, so give it a user that can write documents and create indexes. It is configured with a few extra variables:

```bash
export CB_ADMIN_USER="<a user that can create indexes and search indexes>"
export CB_ADMIN_PASSWORD="<its password>"
export CB_HOST="cb.xxxxxxxx.cloud.couchbase.com"   # host name only, used for the Search REST calls
python embed.py
```

`embed.py` writes the `embedding` field into every hotel document and then creates the vector indexes from [`init/vector/`](../init/vector/).

Notes and things to adjust:

- **Search REST URL.** `embed.py` calls the Search service on port `8094` over plain HTTP, which suits the local container. On Capella the Search service uses HTTPS (port `18094`) and you must allow your IP address. Alternatively, skip that part and create the `hotel_vector` Search index in the Capella UI by importing [`init/vector/hotel_vector.json`](../init/vector/hotel_vector.json).
- **Index definition files.** `embed.py` reads them from the folder named by `INIT_DIR` (default `/init`, where Docker mounts it). Outside Docker run `export INIT_DIR=../init` first.
- **GSI vector indexes** (Hyperscale and Composite) need Couchbase Server 8.0 or later (Capella supports them). Search vector indexes need 7.6 or later.
- **Different embedding model?** Change the model name and `EMBEDDING_DIMS`, and change `"dims"` in [`hotel_vector.json`](../init/vector/hotel_vector.json) and `"dimension"` in [`vector_indexes.n1ql`](../init/vector/vector_indexes.n1ql) to match. All vectors must come from the same model. See [`app/embeddings.py`](../app/embeddings.py) for OpenAI, Amazon Bedrock and Cohere examples.
- **Capella certificates.** Capella connection strings start with `couchbases://` (note the `s`). The SDK trusts the Capella certificate automatically.
