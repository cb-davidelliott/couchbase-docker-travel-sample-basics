import unittest

from timing import Stopwatch, percentiles


class PercentilesTest(unittest.TestCase):
    def test_known_distribution(self):
        stats = percentiles([float(n) for n in range(1, 101)])
        self.assertEqual(stats["min"], 1.0)
        self.assertEqual(stats["max"], 100.0)
        self.assertAlmostEqual(stats["p50"], 50.5)
        self.assertAlmostEqual(stats["p99"], 99.01)

    def test_needs_at_least_two_samples(self):
        with self.assertRaises(ValueError):
            percentiles([1.0])


class StopwatchTest(unittest.TestCase):
    def test_measures_elapsed_milliseconds(self):
        with Stopwatch() as sw:
            sum(range(1000))
        self.assertGreaterEqual(sw.ms, 0.0)


if __name__ == "__main__":
    unittest.main()
