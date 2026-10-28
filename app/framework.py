"""A tiny framework that describes a demo as a list of steps.

A step is an ordinary function that talks to Couchbase and RETURNS what it found
(a Result). It prints nothing. Keeping "what we ask Couchbase" apart from "how we
display the answer" means the demo files read as plain SDK examples, and the same
steps can be shown by more than one front end.
"""

import ast
import inspect
import textwrap
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cached_property
from typing import TYPE_CHECKING, Any

from couchbase.cluster import Cluster

from config import Settings
from timing import Stopwatch

if TYPE_CHECKING:
    from couchbase.scope import Scope


@dataclass
class Table:
    """Rows to display. Each row is a dict; its keys, in order, are the columns."""

    rows: list[dict[str, Any]]
    title: str | None = None
    total: int | None = None  # total matches, when `rows` is only the top of a larger set


@dataclass
class Result:
    """Everything a step found, ready to be displayed.

    Text in `lines` may contain <mark>...</mark> around highlighted words, exactly as
    Couchbase Search returns it.
    """

    statement: str | None = None  # the SQL++ statement or Search request that was run
    language: str = "sql"  # "sql" or "json", used to colour `statement`
    statement_label: str = ""  # a short caption for `statement`, when it needs one
    parameters: dict[str, Any] | None = None  # parameters to show next to the statement
    tables: list[Table] = field(default_factory=list)
    documents: dict[str, dict[str, Any]] = field(default_factory=dict)  # label -> JSON document
    lines: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)  # label -> milliseconds
    side_by_side: bool = False  # show the tables next to each other instead of stacked
    # True when the step makes a single Couchbase call, so the time of the whole step is that
    # call's round trip. Step.run then adds it to `timings`.
    single_call: bool = False


@dataclass
class Context:
    """What every step receives: the settings and an open connection."""

    settings: Settings
    cluster: Cluster

    @cached_property
    def scope(self) -> "Scope":
        """The scope that holds the travel-sample collections: bucket > scope."""
        return self.cluster.bucket(self.settings.bucket).scope(self.settings.scope)

    def keyspace(self, collection: str) -> str:
        return self.settings.keyspace(collection)

    def warm_up(self) -> None:
        """Open the bucket connection and resolve the collection before any step runs.

        The SDK sets these up lazily on first use. Doing it here means the timings shown
        by the steps reflect steady state, not one-time connection setup.
        """
        self.cluster.bucket(self.settings.bucket).ping()
        self.scope.collection("airline").exists("airline_10")


StepFunction = Callable[[Context], Result]


def function_source(func: Callable[..., Any]) -> str:
    """Source code of `func` without its decorators: just the code that runs."""
    source = textwrap.dedent(inspect.getsource(func))
    definition = ast.parse(source).body[0]
    return "\n".join(source.splitlines()[definition.lineno - 1 :])


@dataclass(frozen=True)
class Step:
    id: str  # short name, used with --only
    title: str
    section: str
    why: str  # one line on why this matters
    func: StepFunction
    # The source, captured when the step is defined, so "Show code" can never show something
    # different from the function that runs (even if the file is edited afterwards).
    source: str

    def run(self, ctx: Context) -> Result:
        """Run the step. A step that makes one Couchbase call also gets its round-trip time."""
        with Stopwatch() as sw:
            result = self.func(ctx)
        if result.single_call:
            result.timings["round trip"] = sw.ms
        return result


@dataclass
class Demo:
    """A named, ordered list of steps."""

    title: str
    script: str  # the script that runs this demo, shown to people who want the command line
    subtitle: str = ""
    steps: list[Step] = field(default_factory=list)

    def step(self, id: str, title: str, *, section: str, why: str):
        """Decorator that adds a function to this demo as a step."""

        def register(func: StepFunction) -> StepFunction:
            self.steps.append(Step(id, title, section, why, func, function_source(func)))
            return func

        return register
