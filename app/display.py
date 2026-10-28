"""Show a Search request as the JSON that Couchbase receives.

In Python you build a query object (`MatchQuery(...)`); underneath, the SDK sends the Search
service JSON. This module renders that JSON from the very objects that run, so what a viewer
reads can never drift from what executed, and it is exactly what you would paste into the
Search page of the web console.
"""

import json
from collections.abc import Mapping, Sequence
from typing import Any

from couchbase.search import SearchQuery, TermFacet
from couchbase.vector_search import VectorQuery

SEARCH_REQUEST_LABEL = "Search request (the JSON sent to Couchbase)"

VECTOR_PREVIEW = 3  # numbers of an embedding to show before abbreviating the rest


def _abbreviate(vector: Sequence[float]) -> list[float | str]:
    """The first few numbers of a vector, then a count of the rest."""
    shown: list[float | str] = [round(x, 3) for x in vector[:VECTOR_PREVIEW]]
    hidden = len(vector) - VECTOR_PREVIEW
    return [*shown, f"... {hidden} more"] if hidden > 0 else shown


def _nearest_neighbour(query: VectorQuery) -> dict[str, Any]:
    """A vector query in the Search service's "knn" form (num_candidates is the k)."""
    knn: dict[str, Any] = {
        "field": query.field_name,
        "vector": _abbreviate(query.vector),
        "k": query.num_candidates,
    }
    if query.prefilter:
        knn["filter"] = query.prefilter.encodable
    return knn


def _is_plain(value: Any) -> bool:
    return not isinstance(value, dict | list)


def _format(value: Any, level: int = 0) -> str:
    """Indented JSON, except that objects and arrays holding only plain values stay on one
    line. Search requests nest deeply, and this keeps them short enough to read at a glance."""
    if isinstance(value, dict) and not all(_is_plain(v) for v in value.values()):
        pad = "  " * (level + 1)
        items = [f"{pad}{json.dumps(k)}: {_format(v, level + 1)}" for k, v in value.items()]
        return "{\n" + ",\n".join(items) + "\n" + "  " * level + "}"
    if isinstance(value, list) and not all(_is_plain(v) for v in value):
        pad = "  " * (level + 1)
        return (
            "[\n"
            + ",\n".join(f"{pad}{_format(v, level + 1)}" for v in value)
            + "\n"
            + "  " * level
            + "]"
        )
    return json.dumps(value)


def request_json(
    query: SearchQuery | VectorQuery,
    *,
    facets: Mapping[str, TermFacet] | None = None,
    highlight_fields: Sequence[str] | None = None,
) -> str:
    """The Search request for `query`, as readable JSON ready to display."""
    if isinstance(query, VectorQuery):
        body: dict[str, Any] = {"knn": [_nearest_neighbour(query)]}
    else:
        body = {"query": query.encodable}
    if facets:
        body["facets"] = {name: facet.encodable for name, facet in facets.items()}
    if highlight_fields:
        body["highlight"] = {"style": "html", "fields": list(highlight_fields)}
    return _format(body)
