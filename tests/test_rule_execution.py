import unittest
from unittest.mock import patch

import pandas as pd

from rule_registry import (
    RULE_REGISTRY,
    build_llm_rule_catalog,
    validate_executable_rule,
    validate_rule_for_approval,
)
from scripts.evaluate_rules import EXECUTOR_FUNCTIONS


class RuleExecutionTests(unittest.TestCase):
    def setUp(self):
        self.df = pd.DataFrame(
            {
                "id": ["1", "2", "2"],
                "source": ["S", "S", "S"],
                "category": ["A", "B", "X"],
                "required": ["yes", "", None],
                "code": ["ABC", "A1", None],
                "amount": [10, 101, "bad"],
                "percent": [0, 50, 101],
                "date": ["2026-01-01", "01/02/2026", None],
                "city": ["Boston", "Miami, FL", "Albany 12207"],
                "state": ["MA", "FL 33101", "NY"],
                "zip": ["02108", "33101", "12207"],
                "left": [1, 2, 3],
                "right": [1, 1, 4],
                "status": ["OPEN", "CLOSED", "OPEN"],
                "email": [None, "x@example.com", ""],
                "phone": ["555", None, ""],
                "copy": ["A", "B", "different"],
            }
        )

    def run_rule(self, rule_type, parameters, column_name=None):
        valid, reason, cleaned = validate_executable_rule(
            {"type": rule_type, "parameters": parameters}
        )
        self.assertTrue(valid, reason)
        spec = RULE_REGISTRY[cleaned["type"]]
        executor = EXECUTOR_FUNCTIONS[spec.executor]
        if spec.scope == "COLUMN":
            return executor(
                self.df,
                column_name,
                primary_key="id",
                **cleaned["parameters"],
            )
        return executor(
            self.df,
            primary_key="id",
            **cleaned["parameters"],
        )

    def assert_failures(self, rule_type, parameters, expected, column=None):
        result = self.run_rule(rule_type, parameters, column)
        self.assertEqual(result["failed_count"], expected)

    def test_allowed_values(self):
        self.assert_failures("allowed_values", {"values": ["A", "B"]}, 1, "category")

    def test_not_null(self):
        self.assert_failures("not_null", {}, 2, "required")

    def test_max_length(self):
        self.assert_failures("max_length", {"max_length": 2}, 1, "code")

    def test_min_length(self):
        self.assert_failures("min_length", {"min_length": 3}, 1, "code")

    def test_regex(self):
        self.assert_failures("regex", {"pattern": "[A-Z]{3}"}, 1, "code")

    def test_numeric_range(self):
        self.assert_failures("numeric_range", {"min": 0, "max": 100}, 2, "amount")

    def test_percentage_range(self):
        self.assert_failures("percentage_range", {"min": 0, "max": 100}, 1, "percent")

    def test_date_format(self):
        self.assert_failures("date_format", {"format": "YYYY-MM-DD"}, 1, "date")

    def test_city_location_rule_and_legacy_alias(self):
        self.assert_failures("city_ends_with_state_or_zip", {}, 2, "city")

    def test_state_location_rule(self):
        self.assert_failures("state_field_contains_zip", {}, 1, "state")

    def test_reference_value(self):
        with patch(
            "scripts.evaluate_rules.find_matching_reference_keys",
            return_value={("A",), ("B",)},
        ):
            self.assert_failures(
                "reference_value",
                {"reference_dataset": "categories", "reference_column": "code"},
                1,
                "category",
            )

    def test_column_comparison(self):
        self.assert_failures(
            "column_comparison",
            {"left_column": "left", "operator": "==", "right_column": "right"},
            2,
        )

    def test_conditional_required(self):
        self.assert_failures(
            "conditional_required",
            {
                "condition_column": "status",
                "condition_operator": "==",
                "condition_value": "OPEN",
                "required_column": "email",
            },
            2,
        )

    def test_at_least_one_present(self):
        self.assert_failures("at_least_one_present", {"columns": ["email", "phone"]}, 1)

    def test_columns_equal(self):
        self.assert_failures("columns_equal", {"columns": ["category", "copy"]}, 1)

    def test_reference_combination(self):
        with patch(
            "scripts.evaluate_rules.find_matching_reference_keys",
            return_value={("boston", "ma"), ("miami, fl", "fl 33101")},
        ):
            self.assert_failures(
                "reference_combination",
                {
                    "reference_dataset": "places",
                    "column_mapping": {"city": "place_name", "state": "state_code"},
                },
                1,
            )

    def test_city_state_zip_reference(self):
        with patch(
            "scripts.evaluate_rules.find_matching_reference_keys",
            return_value={
                ("boston", "ma", "02108"),
                ("albany 12207", "ny", "12207"),
            },
        ):
            self.assert_failures(
                "city_state_zip_reference",
                {
                    "city_column": "city",
                    "state_column": "state",
                    "zip_column": "zip",
                },
                1,
            )

    def test_minimum_row_count(self):
        self.assert_failures("minimum_row_count", {"minimum_rows": 4}, 1)

    def test_primary_key_unique(self):
        self.assert_failures("primary_key_unique", {"column": "id"}, 2)

    def test_column_combination_unique(self):
        self.assert_failures("column_combination_unique", {"columns": ["id", "source"]}, 2)

    def test_every_registry_rule_has_a_callable_executor(self):
        for rule_type, spec in RULE_REGISTRY.items():
            with self.subTest(rule_type=rule_type):
                self.assertIsNotNone(spec.executor)
                self.assertTrue(callable(EXECUTOR_FUNCTIONS.get(spec.executor)))

    def test_llm_catalog_only_advertises_executable_rules(self):
        for scope in ("COLUMN", "ROW", "DATASET"):
            catalog = build_llm_rule_catalog(scope)
            for rule_type, spec in RULE_REGISTRY.items():
                if spec.scope == scope and spec.llm_generatable:
                    self.assertIn(f"- {rule_type}", catalog)
                    self.assertTrue(callable(EXECUTOR_FUNCTIONS.get(spec.executor)))

    def test_approval_rejects_unsupported_rule(self):
        valid, reason, _ = validate_rule_for_approval(
            {"executable_rule": {"type": "invented", "parameters": {}}},
            "COLUMN",
        )
        self.assertFalse(valid)
        self.assertIn("Unsupported rule type", reason)

    def test_approval_normalizes_legacy_shape_and_alias(self):
        valid, reason, definition = validate_rule_for_approval(
            {"executable_rule": {"type": "pattern", "pattern": "[A-Z]+"}},
            "COLUMN",
        )
        self.assertTrue(valid, reason)
        self.assertEqual(
            definition["executable_rule"],
            {"type": "regex", "parameters": {"pattern": "[A-Z]+"}},
        )


if __name__ == "__main__":
    unittest.main()
