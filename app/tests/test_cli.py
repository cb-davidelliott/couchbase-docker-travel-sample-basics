import io
import unittest
from contextlib import redirect_stdout

from cli import build_parser, run
from framework import Demo, Result


def make_demo() -> Demo:
    demo = Demo(title="Demo")
    demo.step("one", "First step", section="S", why="w")(lambda ctx: Result())
    return demo


class CliTest(unittest.TestCase):
    def test_defaults(self):
        args = build_parser([make_demo()]).parse_args([])
        self.assertFalse(args.no_pause)
        self.assertEqual(args.rows, 5)
        self.assertEqual(args.only, [])

    def test_list_prints_steps_without_connecting(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = run([make_demo()], ["--list"])
        self.assertEqual(code, 0)
        self.assertIn("one", out.getvalue())
        self.assertIn("First step", out.getvalue())

    def test_unknown_step_is_rejected_without_connecting(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = run([make_demo()], ["--only", "nope"])
        self.assertEqual(code, 2)
        self.assertIn("Unknown step id", out.getvalue())


if __name__ == "__main__":
    unittest.main()
