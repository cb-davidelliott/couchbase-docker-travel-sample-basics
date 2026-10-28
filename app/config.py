"""Settings, read once from environment variables (see the .env file).

Nothing is hard-coded: to point the demos at another cluster, change the
environment variables. See docs/run-locally.md.
"""

import os
from dataclasses import dataclass
from functools import cache
from typing import Self

# Names of the Search indexes created during setup (see init/search and init/vector).
TEXT_INDEX = "hotel_text"
VECTOR_INDEX = "hotel_vector"


def require_env(name: str) -> str:
    """Return an environment variable, or exit with a clear message if it is not set."""
    try:
        return os.environ[name]
    except KeyError:
        raise SystemExit(
            f"The environment variable {name} is not set. See docs/run-locally.md."
        ) from None


@dataclass(frozen=True)
class Settings:
    """Connection details and names shared by every demo."""

    connstr: str
    user: str
    password: str
    bucket: str
    scope: str
    embedding_model: str
    embedding_dims: int

    @classmethod
    def from_env(cls) -> Self:
        return cls(
            connstr=os.environ.get("CB_CONNSTR", "couchbase://localhost"),
            user=require_env("CB_USER"),
            password=require_env("CB_PASSWORD"),
            bucket=os.environ.get("CB_BUCKET", "travel-sample"),
            # In Couchbase, data is organised as bucket > scope > collection (a bit like
            # database > schema > table). travel-sample keeps its data in the "inventory" scope.
            scope="inventory",
            embedding_model=os.environ.get(
                "EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
            ),
            embedding_dims=int(os.environ.get("EMBEDDING_DIMS", "384")),
        )

    def keyspace(self, collection: str) -> str:
        """Full SQL++ name of a collection, e.g. `travel-sample`.inventory.hotel"""
        return f"`{self.bucket}`.{self.scope}.{collection}"


@cache
def get_settings() -> Settings:
    return Settings.from_env()
