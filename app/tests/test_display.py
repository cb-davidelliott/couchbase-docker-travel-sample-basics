import json
import unittest

from couchbase.search import MatchPhraseQuery, MatchQuery, TermFacet, TermQuery
from couchbase.vector_search import VectorQuery

from display import request_json


class RequestJsonTest(unittest.TestCase):
    def test_text_query_is_wrapped_in_query(self):
        body = json.loads(
            request_json(MatchPhraseQuery("continental breakfast", field="description"))
        )
        self.assertEqual(
            body, {"query": {"match_phrase": "continental breakfast", "field": "description"}}
        )

    def test_changing_the_query_changes_the_display(self):
        # The point of generating the JSON: it cannot drift from the query that runs.
        body = json.loads(request_json(MatchQuery("beech", field="description", fuzziness=2)))
        self.assertEqual(body["query"]["fuzziness"], 2)

    def test_facets_and_highlighting_are_included_when_given(self):
        body = json.loads(
            request_json(
                MatchQuery("beach", field="description"),
                facets={"countries": TermFacet("country", 5)},
                highlight_fields=["description"],
            )
        )
        self.assertEqual(body["facets"], {"countries": {"field": "country", "size": 5}})
        self.assertEqual(body["highlight"], {"style": "html", "fields": ["description"]})

    def test_vector_query_is_shown_as_knn_with_an_abbreviated_vector(self):
        query = VectorQuery("embedding", [0.1234567] * 384, num_candidates=5)
        knn = json.loads(request_json(query))["knn"][0]
        self.assertEqual(knn["field"], "embedding")
        self.assertEqual(knn["k"], 5)
        self.assertEqual(knn["vector"], [0.123, 0.123, 0.123, "... 381 more"])
        self.assertNotIn("filter", knn)

    def test_vector_prefilter_is_shown_as_filter(self):
        prefilter = TermQuery("United States", field="country")
        query = VectorQuery("embedding", [0.5] * 4, num_candidates=3, prefilter=prefilter)
        knn = json.loads(request_json(query))["knn"][0]
        self.assertEqual(knn["filter"], {"term": "United States", "field": "country"})
        self.assertEqual(knn["vector"], [0.5, 0.5, 0.5, "... 1 more"])

    def test_short_vector_is_not_abbreviated(self):
        knn = json.loads(request_json(VectorQuery("embedding", [0.5, 0.25], num_candidates=1)))[
            "knn"
        ][0]
        self.assertEqual(knn["vector"], [0.5, 0.25])

    def test_small_objects_stay_on_one_line_so_requests_stay_short(self):
        text = request_json(MatchQuery("beach", field="description"))
        self.assertIn('"query": {"field": "description", "match": "beach"}', text)
        self.assertEqual(len(text.splitlines()), 3)


if __name__ == "__main__":
    unittest.main()
