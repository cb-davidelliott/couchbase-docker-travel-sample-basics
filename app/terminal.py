"""Shows a demo in the terminal, one step at a time.

Steps never print; they return a Result. This module is the only place that decides
how a Result looks, so changing the presentation never touches the demo code.
"""

import json
import sys
from dataclasses import dataclass

from rich.columns import Columns
from rich.console import Console, Group, RenderableType
from rich.markup import escape
from rich.padding import Padding
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table as RichTable
from rich.text import Text

from framework import Context, Demo, Result, Step, Table

THEME = "ansi_dark"
PAUSE_HELP = "[dim]Enter = continue · a = run the rest without pausing · q = quit[/dim]"


@dataclass
class Options:
    pause: bool  # wait for Enter between steps
    rows: int  # maximum rows shown per table
    show_code: bool  # print each step's source code before running it


def interactive() -> bool:
    """True when a person is at the keyboard (so pausing makes sense)."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def _format(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    text = "" if value is None else str(value)
    return text if len(text) <= 70 else text[:67] + "..."


def _highlight(line: str) -> Text:
    """Turn Couchbase's <mark>word</mark> highlighting into bold text."""
    markup = escape(line).replace("<mark>", "[bold yellow]").replace("</mark>", "[/bold yellow]")
    return Text.from_markup(markup)


def _table(table: Table, max_rows: int) -> RenderableType:
    grid = RichTable(
        title=table.title, title_justify="left", header_style="bold", box=None, padding=(0, 2)
    )
    columns = list(table.rows[0]) if table.rows else []
    for column in columns:
        grid.add_column(column)
    for row in table.rows[:max_rows]:
        grid.add_row(*(_format(row[c]) for c in columns))

    shown = min(len(table.rows), max_rows)
    count = f"{len(table.rows)} row(s)"
    if table.total is not None and table.total > shown:
        count = f"top {shown} of {table.total} matches"
    elif len(table.rows) > shown:
        count = f"top {shown} of {len(table.rows)} rows"
    return Group(grid, Text(f"  {count}", style="green"))


class TerminalRunner:
    """Runs demos in a terminal, optionally pausing after each step."""

    def __init__(self, options: Options, console: Console | None = None) -> None:
        self.options = options
        self.console = console or Console(highlight=False)

    def run(self, demo: Demo, ctx: Context, only: frozenset[str] = frozenset()) -> None:
        steps = [s for s in demo.steps if not only or s.id in only]
        if not steps:
            return
        self._banner(demo)
        self.pause("Press Enter to begin.")
        section = None
        for number, step in enumerate(steps, start=1):
            if self.options.pause and self.console.is_terminal:
                self.console.clear()
            if step.section != section:
                section = step.section
                self.console.print()
                self.console.rule(f"[bold red]{section}[/bold red]", align="left")
            self._run_step(step, ctx, number, len(steps))
            if number < len(steps):
                self.pause("Press Enter for the next step.")
        self.console.print(f"\n[bold green]✔ {demo.title} complete.[/bold green]")

    def pause(self, message: str) -> None:
        """Wait for the viewer, if pausing is on. 'a' stops pausing, 'q' quits."""
        if not self.options.pause:
            return
        self.console.print(f"\n[bold]{message}[/bold] {PAUSE_HELP}")
        try:
            answer = self.console.input("› ").strip().lower()
        except EOFError:
            answer = "a"
        if answer == "q":
            raise SystemExit(0)
        if answer == "a":
            self.options.pause = False

    def _banner(self, demo: Demo) -> None:
        body = f"[bold]{demo.title}[/bold]"
        if demo.subtitle:
            body += f"\n[dim]{demo.subtitle}[/dim]"
        self.console.print()
        self.console.print(Panel(body, border_style="red", padding=(1, 2)))

    def _run_step(self, step: Step, ctx: Context, number: int, total: int) -> None:
        self.console.print(f"\n[bold cyan]▶ Step {number}/{total} · {step.title}[/bold cyan]")
        if self.options.show_code:
            self.console.print("  [dim]Code that runs:[/dim]")
            self.console.print(
                Syntax(step.source, "python", theme=THEME, word_wrap=True, padding=(0, 2))
            )
        try:
            result = step.run(ctx)
        except Exception as exc:
            self.console.print(
                Panel(f"{type(exc).__name__}: {exc}", title=f"Step failed: {step.id}", style="red")
            )
            raise SystemExit(1) from exc
        self._show(result)
        self.console.print(
            Padding(
                Text.assemble(("💡 Why this matters: ", "yellow"), step.why),
                (0, 2),
            )
        )

    def _show(self, result: Result) -> None:
        console = self.console
        if result.statement and result.statement_label:
            console.print(f"  [dim]{result.statement_label}[/dim]")
        if result.statement:
            console.print(
                Syntax(
                    result.statement,
                    result.language,
                    theme=THEME,
                    word_wrap=True,
                    padding=(0, 2),
                )
            )
        if result.parameters:
            console.print(f"  [dim]parameters: {result.parameters}[/dim]")
        for label, document in result.documents.items():
            console.print(f"  [bold]{label}[/bold]")
            console.print(
                Syntax(json.dumps(document, indent=2), "json", theme=THEME, padding=(0, 2))
            )
        if result.tables:
            rendered = [_table(t, self.options.rows) for t in result.tables]
            console.print(
                Columns(rendered, padding=(0, 4)) if result.side_by_side else Group(*rendered)
            )
        for line in result.lines:
            console.print(Padding(_highlight(line), (0, 2)))
        if result.timings:
            parts = (
                f"{label} [bold green]{ms:.1f} ms[/bold green]"
                for label, ms in result.timings.items()
            )
            console.print("  ⏱  " + " · ".join(parts))
