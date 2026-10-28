"""Small timing helpers."""

import statistics
import time
from collections.abc import Sequence
from types import TracebackType
from typing import Self


class Stopwatch:
    """Measure wall-clock time in milliseconds.

    with Stopwatch() as sw:
        do_something()
    print(sw.ms)
    """

    ms: float = 0.0

    def __enter__(self) -> Self:
        self._start = time.perf_counter()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.ms = (time.perf_counter() - self._start) * 1000


def percentiles(samples: Sequence[float]) -> dict[str, float]:
    """Summarise latency samples (in ms) as min, p50, p95, p99 and max."""
    if len(samples) < 2:
        raise ValueError("need at least two samples")
    cuts = statistics.quantiles(samples, n=100, method="inclusive")
    return {
        "min": min(samples),
        "p50": cuts[49],
        "p95": cuts[94],
        "p99": cuts[98],
        "max": max(samples),
    }
