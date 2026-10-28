import unittest

import numpy as np

from deepdive import exact_top_k, recall_at_k, summarize_plan


class PlanSummaryTest(unittest.TestCase):
    def test_collects_indexes_and_operators_without_duplicates(self):
        plan = {
            "#operator": "Sequence",
            "~children": [
                {"#operator": "IndexScan3", "index": "idx_a", "keyspace": "hotel"},
                {"#operator": "Fetch"},
                {"#operator": "IndexScan3", "index": "idx_a"},
                {"#operator": "IndexScan3", "index": "idx_b"},
            ],
        }
        summary = summarize_plan(plan)
        self.assertEqual(summary.indexes, ["idx_a", "idx_b"])
        self.assertEqual(summary.operators, ["Sequence", "IndexScan3", "Fetch"])

    def test_plan_without_index_reports_none(self):
        self.assertEqual(summarize_plan({"#operator": "PrimaryScan3"}).indexes, [])


class RecallTest(unittest.TestCase):
    def test_exact_top_k_orders_by_similarity(self):
        matrix = np.array([[1.0, 0.0], [0.0, 1.0], [0.8, 0.6]])
        self.assertEqual(exact_top_k(matrix, ["x", "y", "z"], [1.0, 0.0], k=2), ["x", "z"])

    def test_recall_is_the_overlap_fraction(self):
        self.assertEqual(recall_at_k(["a", "b", "c", "d"], ["a", "b", "x", "y"]), 0.5)
        self.assertEqual(recall_at_k(["a", "b"], ["b", "a"]), 1.0)


if __name__ == "__main__":
    unittest.main()
