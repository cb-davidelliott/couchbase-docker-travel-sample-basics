import unittest

from framework import Demo, Result, function_source


class FunctionSourceTest(unittest.TestCase):
    def test_strips_decorators_and_keeps_the_body(self):
        demo = Demo(title="t", script="t.py")

        @demo.step(
            "one",
            "First",
            section="S",
            why="because",
        )
        def first(ctx):
            return Result(lines=["hello"])

        source = function_source(first)
        self.assertTrue(source.startswith("def first(ctx):"))
        self.assertIn('Result(lines=["hello"])', source)
        self.assertNotIn("@demo.step", source)

    def test_step_decorator_registers_in_order(self):
        demo = Demo(title="t", script="t.py")

        @demo.step("a", "A", section="S", why="w")
        def a(ctx):
            return Result()

        @demo.step("b", "B", section="S", why="w")
        def b(ctx):
            return Result()

        self.assertEqual([s.id for s in demo.steps], ["a", "b"])
        self.assertIs(demo.steps[0].func, a)

    def test_source_is_captured_when_the_step_is_defined(self):
        demo = Demo(title="t", script="t.py")

        @demo.step("a", "A", section="S", why="w")
        def a(ctx):
            return Result()

        self.assertTrue(demo.steps[0].source.startswith("def a(ctx):"))


if __name__ == "__main__":
    unittest.main()
