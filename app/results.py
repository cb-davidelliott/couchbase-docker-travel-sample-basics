"""Turn what the SDK returned into something that can be displayed.

A demo step makes the real SDK call itself, then hands the result to one of these
functions. They only PRESENT what came back (rows, hits, facets, timings); they never
talk to Couchbase. That keeps each step down to the call you would write in your own
application.
"""

import textwrap
from collections.abc import Sequence
from typing import Any

from couchbase.result import QueryResult, SearchResult

from display import SEARCH_REQUEST_LABEL
from framework import Result, Table


def from_query(
    statement: str,
    result: QueryResult,
    *,
    columns: Sequence[str] | None = None,
    parameters: dict[str, Any] | None = None,
) -> Result:
    """Present the rows of a SQL++ query.

    `columns` sets the column order (Couchbase returns JSON keys alphabetically).
    `parameters` is shown next to the statement. Server time is included when the
    query was run with QueryOptions(metrics=True).
    """
    rows = list(result)  # rows stream in as we read them
    if columns:
        rows = [{c: row.get(c) for c in columns} for row in rows]
    timings = {}
    if (metrics := result.metadata().metrics()) is not None:
        timings["server"] = metrics.execution_time().total_seconds() * 1000
    return Result(
        statement=textwrap.dedent(statement).strip(),
        parameters=parameters,
        tables=[Table(rows)],
        timings=timings,
        single_call=True,
    )


def _search_table(
    hits: list[Any], total: int, fields: Sequence[str], title: str | None, score_label: str
) -> Table:
    # The score is relevance for a text search, and cosine similarity for a vector search.
    rows = [{score_label: round(h.score, 3), **{f: h.fields.get(f) for f in fields}} for h in hits]
    return Table(rows, title=title, total=total)


def search_table(
    result: SearchResult,
    fields: Sequence[str],
    *,
    title: str | None = None,
    score_label: str = "score",
) -> Table:
    """Present the hits of a Search query as one table, to show several side by side."""
    hits = list(result)
    return _search_table(hits, result.metadata().metrics().total_rows(), fields, title, score_label)


def from_search(
    request: str,
    result: SearchResult,
    *,
    fields: Sequence[str],
    score_label: str = "score",
) -> Result:
    """Present the hits of a Search query: scores, highlighted snippets and facet counts.

    `request` is the Search request JSON to show (see display.request_json). The first of
    `fields` names each hit. Highlights and term facets appear when they were requested.
    """
    hits = list(result)
    total = result.metadata().metrics().total_rows()

    lines = []
    if any(hit.fragments for hit in hits):
        lines.append("Why each hit matched:")
        for hit in hits:
            snippet = " … ".join(sum((hit.fragments or {}).values(), []))
            lines.append(f"• {hit.fields.get(fields[0])}: {snippet}")
    for name, facet in (result.facets() or {}).items():
        lines.append(f"{name}: " + ", ".join(f"{t.term} ({t.count})" for t in facet.terms))

    return Result(
        statement=request,
        language="json",
        statement_label=SEARCH_REQUEST_LABEL,
        tables=[_search_table(hits, total, fields, None, score_label)],
        lines=lines,
        single_call=True,
    )
