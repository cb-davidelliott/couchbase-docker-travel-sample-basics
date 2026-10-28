"""Connecting to Couchbase and running SQL++ with timing."""

import logging
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from couchbase.auth import PasswordAuthenticator
from couchbase.cluster import Cluster
from couchbase.options import ClusterOptions, ClusterTimeoutOptions, QueryOptions

from config import Settings
from timing import Stopwatch

log = logging.getLogger(__name__)

CONNECT_ATTEMPTS = 3


def connect(settings: Settings, credentials: tuple[str, str] | None = None) -> Cluster:
    """Open a connection to the cluster and wait until it is ready.

    The SDK discovers every node and service from the address you give it, then
    talks directly to the right node for each request. By default this logs in
    as the limited demo user; only the one-time embed step passes administrator
    credentials, because it creates indexes.
    """
    user, password = credentials or (settings.user, settings.password)
    options = ClusterOptions(
        PasswordAuthenticator(user, password),
        # Be patient on the first connection: a freshly started server can take a
        # few seconds to hand out its cluster map.
        timeout_options=ClusterTimeoutOptions(connect_timeout=timedelta(seconds=20)),
    )
    last_error: Exception | None = None
    for attempt in range(1, CONNECT_ATTEMPTS + 1):
        try:
            cluster = Cluster(settings.connstr, options)
            cluster.wait_until_ready(timedelta(seconds=30))
            return cluster
        except Exception as exc:  # any connect failure is worth a retry
            last_error = exc
            log.warning("Could not connect yet (attempt %d/%d)", attempt, CONNECT_ATTEMPTS)
            time.sleep(3)
    raise SystemExit(f"Could not connect to Couchbase at {settings.connstr}: {last_error}")


@dataclass(frozen=True)
class QueryOutcome:
    """The rows of a SQL++ query and how long it took."""

    rows: list[dict[str, Any]]
    server_ms: float  # time the Query service spent executing the statement
    round_trip_ms: float  # total time seen by this application, network included


def run_sqlpp(
    cluster: Cluster, statement: str, parameters: dict[str, Any] | None = None
) -> QueryOutcome:
    """Run a SQL++ statement with named parameters and return its rows with timings."""
    # metrics=True asks the server to report how long it spent on the statement.
    options = QueryOptions(named_parameters=parameters or {}, metrics=True)
    with Stopwatch() as sw:
        result = cluster.query(statement, options)
        rows = list(result)
    server_ms = result.metadata().metrics().execution_time().total_seconds() * 1000
    return QueryOutcome(rows, server_ms, sw.ms)
