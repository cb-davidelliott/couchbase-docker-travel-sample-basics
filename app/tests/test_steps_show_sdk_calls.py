"""A guard for what "Show code" promises: the code you see is the real SDK call.

If a step hides its call behind a helper, a developer reading it learns nothing about the
SDK. This test fails when any step's displayed source has no Couchbase call in it.
"""

import re
import unittest

from catalog import DEMOS

# Key-Value operations, SQL++, Search, and (for the embedding step) the embedding call itself.
SDK_CALL = re.compile(
    r"\.(query|search|get|upsert|mutate_in|lookup_in|remove)\(|\bembed_text\(",
)


class StepsShowTheirSdkCallTest(unittest.TestCase):
    def test_every_step_shows_the_call_it_demonstrates(self):
        for demo in DEMOS:
            for step in demo.steps:
                with self.subTest(step=step.id):
                    self.assertRegex(
                        step.source,
                        SDK_CALL,
                        f"step '{step.id}' does not show an SDK call; put the call in the step, "
                        "not in a helper",
                    )


if __name__ == "__main__":
    unittest.main()
