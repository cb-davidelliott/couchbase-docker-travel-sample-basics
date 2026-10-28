"""The demos, in the order they are presented. Shared by the CLI and the web UI."""

from framework import Demo
from kv_and_queries import DEMO as KV_AND_QUERIES
from search_demo import DEMO as SEARCH
from vector_demo import DEMO as VECTOR

DEMOS: list[Demo] = [KV_AND_QUERIES, SEARCH, VECTOR]
