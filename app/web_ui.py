"""Web UI: a guided tour of the demos, and a keyword-vs-vector playground.

Everything on screen runs live against the Couchbase cluster. The tour runs the very same
step functions as the command-line demos, and "Show code" displays their actual source.

Started by the `web` service in docker-compose.yml:   streamlit run web_ui.py
"""

import html
import os
from datetime import date
from pathlib import Path

import streamlit as st

import playground
from catalog import DEMOS
from client import connect
from config import get_settings
from embeddings import embed_text
from framework import Context, Demo, Result, Step, function_source

CONSOLE_URL = f"http://localhost:{os.environ.get('CB_PORT_UI', '8091')}"
EXAMPLE_QUESTIONS = (
    "quiet beachfront place good for families with young kids",
    "somewhere to unwind after a long day hiking in the mountains",
    "a romantic getaway with a view of the sea",
    "cheap hostel for backpackers near the city centre",
)

# The Couchbase logo and tab icon, stored in the repo so the page needs no internet access.
ASSETS = Path(__file__).parent / "assets"

st.set_page_config(
    page_title="Couchbase demo", page_icon=str(ASSETS / "couchbase-favicon.svg"), layout="wide"
)
st.logo(str(ASSETS / "couchbase-logo.svg"), size="large", link="https://www.couchbase.com")


def code_version() -> str:
    """Changes whenever a source file in this folder is edited."""
    return str(max(path.stat().st_mtime_ns for path in Path(__file__).parent.glob("*.py")))


@st.cache_resource
def started_with() -> str:
    """The code version when this server process started (remembered for the process)."""
    return code_version()


@st.cache_resource(show_spinner="Connecting to Couchbase...")
def get_context() -> Context:
    """One connection (and one loaded embedding model) shared by every browser session."""
    settings = get_settings()
    ctx = Context(settings, connect(settings))
    ctx.warm_up()
    embed_text("warm up")
    return ctx


# ------------------------------------------------------------------------------ results
def run_steps(ctx: Context, steps: list[Step]) -> None:
    """Button callback: run steps and remember what each returned (or the error it raised)."""
    for step in steps:
        try:
            st.session_state.outcomes[step.id] = step.run(ctx)
        except Exception as exc:  # shown to the viewer next to the step
            st.session_state.outcomes[step.id] = exc


def show_text(line: str) -> None:
    """Show a line of text, turning Couchbase's <mark> highlighting into a highlight."""
    safe = html.escape(line).replace("&lt;mark&gt;", "<mark>").replace("&lt;/mark&gt;", "</mark>")
    st.markdown(safe, unsafe_allow_html=True)


def show_result(result: Result) -> None:
    if result.statement_label:
        st.caption(result.statement_label)
    if result.statement:
        st.code(result.statement, language=result.language, wrap_lines=True)
    if result.parameters:
        st.caption(f"parameters: {result.parameters}")
    for label, document in result.documents.items():
        st.caption(label)
        st.json(document)

    columns = (
        st.columns(len(result.tables))
        if result.side_by_side
        else [st.container()] * len(result.tables)
    )
    for table, column in zip(result.tables, columns, strict=True):
        with column:
            if table.title:
                st.markdown(f"**{table.title}**")
            st.dataframe(table.rows, hide_index=True, width="stretch")
            shown = len(table.rows)
            st.caption(
                f"top {shown} of {table.total} matches"
                if table.total and table.total > shown
                else f"{shown} row(s)"
            )
    for line in result.lines:
        show_text(line)
    if result.timings:
        show_timings(result.timings)


def show_timings(timings: dict[str, float]) -> None:
    st.caption("⏱ " + " · ".join(f"{label} **{ms:.1f} ms**" for label, ms in timings.items()))


# --------------------------------------------------------------------------- guided tour
def show_step(ctx: Context, demo: Demo, number: int, step: Step) -> None:
    with st.container(border=True):
        st.markdown(f"**{number}. {step.title}**")
        st.caption(f"💡 {step.why}")
        run_column, code_column = st.columns([1, 5])
        run_column.button("▶ Run", key=f"run:{step.id}", on_click=run_steps, args=(ctx, [step]))
        if code_column.toggle("Show code", key=f"code:{step.id}"):
            st.code(step.source, language="python")

        outcome = st.session_state.outcomes.get(step.id)
        if isinstance(outcome, Exception):
            st.error(f"{type(outcome).__name__}: {outcome}")
        elif outcome is not None:
            show_result(outcome)

        with st.expander("Run this step in a terminal"):
            command = (
                f"docker compose run --rm app python {demo.script} --only {step.id} --show-code"
            )
            st.code(command, language="bash")


def show_tour(ctx: Context) -> None:
    by_title = {demo.title: demo for demo in DEMOS}
    demo = by_title[st.radio("Demo", list(by_title), horizontal=True, key="demo")]
    st.caption(demo.subtitle)
    st.button(
        "▶ Run all steps", key=f"run-all:{demo.script}", on_click=run_steps, args=(ctx, demo.steps)
    )

    section = None
    for number, step in enumerate(demo.steps, start=1):
        if step.section != section:
            section = step.section
            st.subheader(section)
        show_step(ctx, demo, number, step)


# ------------------------------------------------------------------------------ playground
def show_hits(hits: list[playground.Hit], score_label: str) -> None:
    """One entry per hotel: name, place and score, with the description wrapped beneath."""
    for hit in hits:
        st.markdown(f"**{hit.name}** · {hit.city}, {hit.country} · {score_label} `{hit.score}`")
        st.caption(hit.description)


def show_playground(ctx: Context) -> None:
    st.markdown(
        "Ask a question in your own words. The same question is answered two ways: **keyword "
        "search** (matches the words) and **vector search** (matches the meaning)."
    )
    st.caption("Try an example:")
    for column, example in zip(st.columns(len(EXAMPLE_QUESTIONS)), EXAMPLE_QUESTIONS, strict=True):
        column.button(example, on_click=st.session_state.update, kwargs={"question": example})

    with st.form("playground"):
        st.text_area("Your question", key="question", height=70)
        country_column, pets_column, breakfast_column, count_column = st.columns(4)
        country = country_column.selectbox("Country", ["Any", *playground.countries(ctx)])
        pets_ok = pets_column.checkbox("Pets allowed")
        free_breakfast = breakfast_column.checkbox("Free breakfast")
        k = count_column.slider("Results", 3, 10, 5)
        submitted = st.form_submit_button("Search", type="primary")

    question = st.session_state.question.strip()
    if submitted and question:
        filters = playground.Filters(None if country == "Any" else country, pets_ok, free_breakfast)
        st.session_state.comparison = playground.compare(ctx, question, filters, k)

    comparison = st.session_state.get("comparison")
    if comparison is None:
        return
    keyword_column, vector_column = st.columns(2)
    with keyword_column:
        st.subheader("Keyword search")
        st.caption(f"{comparison.keyword_total} hotels match some of the words")
        show_timings({"search": comparison.keyword_ms})
        show_hits(comparison.keyword, "score")
    with vector_column:
        st.subheader("Vector search")
        st.caption("the nearest hotels by meaning (similarity: 1.0 = identical)")
        show_timings({"embed the question": comparison.embed_ms, "search": comparison.vector_ms})
        show_hits(comparison.vector, "similarity")
    with st.expander("Show code"):
        st.code(function_source(playground.compare), language="python")


# --------------------------------------------------------------------------------- footer
def show_footer() -> None:
    """Notices: the trademark line (wording from couchbase.com) and the server edition terms."""
    st.divider()
    st.caption(
        f"© {date.today().year} Couchbase, Inc. Couchbase, the Couchbase logo and Couchbase. "
        "The Operational Data Platform for AI. are registered trademarks of Couchbase, Inc. "
        "All rights reserved."
    )
    st.caption(
        "This demo runs Couchbase Server Enterprise Edition, which is free for development and "
        "testing. A paid subscription is required for production."
    )


# ---------------------------------------------------------------------------------- page
def main() -> None:
    st.session_state.setdefault("outcomes", {})
    st.session_state.setdefault("question", EXAMPLE_QUESTIONS[0])

    st.title("Couchbase travel-sample demo")
    st.caption(
        f"Live against your local cluster. The Couchbase web console is at {CONSOLE_URL} "
        "(Administrator / password123)."
    )
    if code_version() != started_with():
        st.warning(
            "The code changed after this page started, so it is still showing and running the "
            "old version. Restart the UI to pick up your edits: `docker compose restart web`"
        )
    ctx = get_context()
    tour, playground_tab = st.tabs(["Guided tour", "Playground"])
    with tour:
        show_tour(ctx)
    with playground_tab:
        show_playground(ctx)
    show_footer()


main()
