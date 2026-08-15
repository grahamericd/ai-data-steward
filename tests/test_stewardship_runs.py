import os
import unittest
from unittest.mock import patch

from scripts.run_pipeline import PIPELINE_PHASES, run_script
from stewardship_context import get_stewardship_run_id


class StewardshipRunTests(unittest.TestCase):
    def test_pipeline_contains_every_required_phase(self):
        self.assertEqual(
            [phase for phase, _ in PIPELINE_PHASES],
            ["load", "profiling", "rule_generation", "evaluation", "remediation"],
        )

    def test_run_context_parses_propagated_identifier(self):
        with patch.dict(os.environ, {"AI_STEWARD_RUN_ID": "1007"}):
            self.assertEqual(get_stewardship_run_id(), 1007)

    def test_child_script_receives_run_and_actor_context(self):
        class Completed:
            stdout = ""
            stderr = ""
            returncode = 0

        with patch("scripts.run_pipeline.subprocess.run", return_value=Completed()) as called:
            run_script("profile_dataset.py", "customers", 1007, "alice@example.com")

        environment = called.call_args.kwargs["env"]
        self.assertEqual(environment["AI_STEWARD_RUN_ID"], "1007")
        self.assertEqual(environment["AI_STEWARD_ACTOR"], "alice@example.com")


if __name__ == "__main__":
    unittest.main()
