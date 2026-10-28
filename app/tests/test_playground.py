import unittest

from playground import Filters


class FiltersTest(unittest.TestCase):
    def test_no_filters_means_no_query(self):
        self.assertIsNone(Filters().query())

    def test_single_filter_is_not_wrapped(self):
        query = Filters(country="France").query()
        self.assertEqual(query.encodable["term"], "France")
        self.assertEqual(query.encodable["field"], "country")

    def test_several_filters_are_combined_with_and(self):
        query = Filters(country="France", pets_ok=True, free_breakfast=True).query()
        fields = [clause["field"] for clause in query.encodable["conjuncts"]]
        self.assertEqual(fields, ["country", "pets_ok", "free_breakfast"])


if __name__ == "__main__":
    unittest.main()
