import unittest
from datetime import timedelta
from types import SimpleNamespace

from framework import Context, Demo, Result
from results import from_query, from_search, search_table


class FakeQueryResult:
    """Just enough of the SDK's QueryResult for the converters."""

    def __init__(self, rows, server_ms=None):
        self.rows, self.server_ms = rows, server_ms

    def __iter__(self):
        return iter(self.rows)

    def metadata(self):
        return self

    def metrics(self):
        return None if self.server_ms is None else self

    def execution_time(self):
        return timedelta(milliseconds=self.server_ms)


def hit(score, fragments=None, **fields):
    return SimpleNamespace(score=score, fields=fields, fragments=fragments or {})


class FakeSearchResult:
    def __init__(self, hits, total, facets=None):
        self.hits, self.total, self._facets = hits, total, facets

    def __iter__(self):
        return iter(self.hits)

    def metadata(self):
        return SimpleNamespace(metrics=lambda: SimpleNamespace(total_rows=lambda: self.total))

    def facets(self):
        return self._facets


class FromQueryTest(unittest.TestCase):
    def test_orders_columns_dedents_the_statement_and_reports_server_time(self):
        result = FakeQueryResult([{"b": 2, "a": 1}], server_ms=3.5)
        shown = from_query("\n        SELECT a, b\n        FROM t", result, columns=["a", "b"])
        self.assertEqual(shown.statement, "SELECT a, b\nFROM t")
        self.assertEqual(list(shown.tables[0].rows[0]), ["a", "b"])
        self.assertEqual(shown.timings, {"server": 3.5})
        self.assertTrue(shown.single_call)

    def test_without_metrics_there_is_no_server_time(self):
        shown = from_query("SELECT 1", FakeQueryResult([{"x": 1}]))
        self.assertEqual(shown.timings, {})


class FromSearchTest(unittest.TestCase):
    def test_rows_carry_the_rounded_score_and_the_total_matches(self):
        result = FakeSearchResult([hit(2.24567, name="Ann Inn", city="Rome")], total=50)
        shown = from_search("{}", result, fields=["name", "city"])
        self.assertEqual(
            shown.tables[0].rows, [{"score": 2.246, "name": "Ann Inn", "city": "Rome"}]
        )
        self.assertEqual(shown.tables[0].total, 50)
        self.assertEqual(shown.language, "json")
        self.assertEqual(shown.lines, [])

    def test_highlights_and_facets_appear_when_requested(self):
        facet = SimpleNamespace(terms=[SimpleNamespace(term="France", count=3)])
        result = FakeSearchResult(
            [hit(1.0, {"description": ["on the <mark>beach</mark>"]}, name="Ann Inn")],
            total=1,
            facets={"countries": facet},
        )
        shown = from_search("{}", result, fields=["name"])
        self.assertIn("• Ann Inn: on the <mark>beach</mark>", shown.lines)
        self.assertIn("countries: France (3)", shown.lines)

    def test_score_label_can_be_renamed(self):
        result = FakeSearchResult([hit(0.5, name="Ann Inn")], total=1)
        table = search_table(result, ["name"], title="Vector", score_label="similarity")
        self.assertEqual(list(table.rows[0]), ["similarity", "name"])
        self.assertEqual(table.title, "Vector")


class StepRunTest(unittest.TestCase):
    def run_step(self, returns: Result) -> Result:
        demo = Demo(title="t", script="t.py")
        demo.step("s", "S", section="x", why="w")(lambda ctx: returns)
        return demo.steps[0].run(Context(settings=None, cluster=None))

    def test_a_single_call_step_gets_its_round_trip_time(self):
        result = self.run_step(Result(single_call=True, timings={"server": 1.0}))
        self.assertEqual(list(result.timings), ["server", "round trip"])

    def test_a_multi_call_step_keeps_only_its_own_timings(self):
        result = self.run_step(Result(timings={"get": 1.0, "upsert": 2.0}))
        self.assertEqual(list(result.timings), ["get", "upsert"])


if __name__ == "__main__":
    unittest.main()
