"""Smoke test for the web UI. It talks to a real cluster, so run it after `docker compose up`:

docker compose run --rm app python -m unittest discover -s integration -t .
"""

import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

from catalog import DEMOS

WEB_UI = Path(__file__).parent.parent / "web_ui.py"


class WebUiTest(unittest.TestCase):
    def setUp(self):
        self.app = AppTest.from_file(str(WEB_UI), default_timeout=120).run()

    def assert_healthy(self):
        self.assertFalse(self.app.exception, [e.value for e in self.app.exception])
        self.assertFalse(self.app.error, [e.value for e in self.app.error])

    def test_page_loads(self):
        self.assert_healthy()

    def test_every_step_of_every_demo_runs(self):
        for demo in DEMOS:
            with self.subTest(demo=demo.title):
                self.app.radio(key="demo").set_value(demo.title).run()
                self.app.button(key=f"run-all:{demo.script}").click().run()
                self.assert_healthy()
                outcomes = self.app.session_state.outcomes
                missing = [s.id for s in demo.steps if s.id not in outcomes]
                self.assertEqual(missing, [])

    def test_playground_compares_keyword_and_vector_search(self):
        search = next(b for b in self.app.button if b.label == "Search")
        search.click().run()
        self.assert_healthy()
        comparison = self.app.session_state.comparison
        self.assertTrue(comparison.keyword)
        self.assertTrue(comparison.vector)
        titles = {subheader.value for subheader in self.app.subheader}
        self.assertLessEqual({"Keyword search", "Vector search"}, titles)


if __name__ == "__main__":
    unittest.main()
