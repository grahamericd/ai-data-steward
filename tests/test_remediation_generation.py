import unittest

from scripts.generate_remediation_suggestions import (
    deterministic_location_suggestion,
    insert_suggestion,
)


class RemediationGenerationTests(unittest.TestCase):
    def test_city_state_is_split_when_destination_is_blank(self):
        suggestion = deterministic_location_suggestion(
            "city_contains_state_or_zip",
            "municipality",
            {},
            {"municipality": "Miami, FL", "state": "", "zip": "33101"},
        )

        self.assertEqual(suggestion["generation_method"], "deterministic")
        self.assertEqual(
            suggestion["suggested_values"],
            {"municipality": "Miami", "state": "FL"},
        )

    def test_city_state_is_not_split_over_existing_state(self):
        suggestion = deterministic_location_suggestion(
            "city_contains_state_or_zip",
            "city",
            {},
            {"city": "Miami, FL", "state": "GA", "zip": ""},
        )
        self.assertIsNone(suggestion)

    def test_state_zip_is_split_when_lossless(self):
        suggestion = deterministic_location_suggestion(
            "state_field_contains_zip",
            "region",
            {},
            {"region": "FL 33101", "postal_code": None},
        )
        self.assertEqual(
            suggestion["suggested_values"],
            {"region": "FL", "postal_code": "33101"},
        )

    def test_unsupported_failure_does_not_guess(self):
        suggestion = deterministic_location_suggestion(
            "allowed_values",
            "status",
            {"values": ["A", "B"]},
            {"status": "C"},
        )
        self.assertIsNone(suggestion)

    def test_insert_carries_complete_failure_lineage(self):
        class FakeResult:
            rowcount = 1

        class FakeConnection:
            def __init__(self):
                self.parameters = None

            def execute(self, statement, parameters):
                self.parameters = parameters
                return FakeResult()

        connection = FakeConnection()
        insert_suggestion(
            connection,
            {
                "dataset_name": "customers",
                "raw_schema": "raw",
                "raw_table": "customer",
            },
            {
                "rule_id": 27,
                "result_id": 891,
                "failed_record_id": 3001,
                "source_row_identifier": "ABC123",
            },
            {
                "issue_type": "city_contains_state",
                "generation_method": "deterministic",
                "original_values": {"city": "Miami FL"},
                "suggested_values": {"city": "Miami", "state": "FL"},
                "confidence_score": 0.95,
            },
        )

        self.assertEqual(connection.parameters["rule_id"], 27)
        self.assertEqual(connection.parameters["result_id"], 891)
        self.assertEqual(connection.parameters["failed_record_id"], 3001)
        self.assertEqual(
            connection.parameters["source_row_identifier"],
            "ABC123",
        )


if __name__ == "__main__":
    unittest.main()
