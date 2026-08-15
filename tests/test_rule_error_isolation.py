import unittest
from unittest.mock import patch

import pandas as pd

from scripts.evaluate_rules import EXECUTOR_FUNCTIONS, evaluate_rule, write_result


class RuleErrorIsolationTests(unittest.TestCase):
    def setUp(self):
        self.df = pd.DataFrame({"id": [1, 2], "value": ["A", None]})

    def rule(self, rule_id, rule_type, parameters=None):
        return {
            "id": rule_id,
            "column_name": "value",
            "rule_type": rule_type,
            "rule_scope": "COLUMN",
            "rule_definition": {
                "executable_rule": {
                    "type": rule_type,
                    "parameters": parameters or {},
                }
            },
        }

    def test_malformed_rule_returns_error_with_diagnostic_details(self):
        status, details = evaluate_rule(
            self.rule(1, "unsupported_rule"),
            self.df,
            primary_key="id",
        )

        self.assertEqual(status, "ERROR")
        self.assertTrue(details["evaluation_failed"])
        self.assertEqual(details["error_type"], "ValueError")
        self.assertIn("Unsupported rule type", details["message"])

    def test_executor_exception_returns_error(self):
        status, details = evaluate_rule(
            self.rule(1, "regex", {"pattern": "["}),
            self.df,
        )

        self.assertEqual(status, "ERROR")
        self.assertIn("unterminated character set", details["message"])

    def test_broken_executor_output_is_not_treated_as_pass(self):
        with patch.dict(
            EXECUTOR_FUNCTIONS,
            {"evaluate_not_null": lambda *args, **kwargs: {}},
        ):
            status, details = evaluate_rule(
                self.rule(1, "not_null"),
                self.df,
            )

        self.assertEqual(status, "ERROR")
        self.assertIn("missing failed_count", details["message"])

    def test_rule_after_broken_rule_still_runs(self):
        rules = [
            self.rule(1, "unsupported_rule"),
            self.rule(2, "not_null"),
        ]

        results = [
            evaluate_rule(rule, self.df, primary_key="id")
            for rule in rules
        ]

        self.assertEqual(results[0][0], "ERROR")
        self.assertEqual(results[1][0], "FAIL")
        self.assertEqual(results[1][1]["failed_count"], 1)

    def test_all_failed_identifiers_are_kept_beyond_display_sample(self):
        dataframe = pd.DataFrame(
            {
                "id": [str(value) for value in range(12)],
                "value": [None] * 12,
            }
        )
        status, details = evaluate_rule(
            self.rule(1, "not_null"),
            dataframe,
            primary_key="id",
        )

        self.assertEqual(status, "FAIL")
        self.assertEqual(details["failed_count"], 12)
        self.assertEqual(len(details["sample_failures"]), 10)
        self.assertEqual(
            details["_failed_row_identifiers"],
            [str(value) for value in range(12)],
        )

    def test_result_and_failed_records_are_written_in_one_transaction(self):
        class FakeResult:
            def scalar_one(self):
                return 42

        class FakeConnection:
            def __init__(self):
                self.calls = []

            def execute(self, statement, parameters):
                self.calls.append((str(statement), parameters))
                return FakeResult()

        class FakeTransaction:
            def __init__(self, connection):
                self.connection = connection

            def __enter__(self):
                return self.connection

            def __exit__(self, exc_type, exc, traceback):
                return False

        connection = FakeConnection()

        class FakeEngine:
            def begin(self):
                return FakeTransaction(connection)

        with patch("scripts.evaluate_rules.engine", FakeEngine()):
            result_id = write_result(
                "customers",
                7,
                "FAIL",
                {
                    "failed_count": 2,
                    "sample_failures": [{"id": "A"}],
                    "_failed_row_identifiers": ["A", "B"],
                },
            )

        self.assertEqual(result_id, 42)
        self.assertEqual(len(connection.calls), 2)
        self.assertIn("INSERT INTO dq.result", connection.calls[0][0])
        self.assertNotIn(
            "_failed_row_identifiers",
            connection.calls[0][1]["details"],
        )
        self.assertIn("INSERT INTO dq.failed_record", connection.calls[1][0])
        self.assertEqual(
            [row["source_row_identifier"] for row in connection.calls[1][1]],
            ["A", "B"],
        )


if __name__ == "__main__":
    unittest.main()
