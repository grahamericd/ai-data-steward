import unittest

from remediation_decision import change_remediation_status
from rule_approval import change_rule_status


class FakeMappingResult:
    def __init__(self, row=None):
        self.row = row

    def mappings(self):
        return self

    def first(self):
        return self.row


class FakeConnection:
    def __init__(self, selected_row):
        self.selected_row = selected_row
        self.calls = []

    def execute(self, statement, parameters):
        sql = str(statement)
        self.calls.append((sql, parameters))
        if "SELECT" in sql:
            return FakeMappingResult(self.selected_row)
        return FakeMappingResult()


class StewardshipAuditTests(unittest.TestCase):
    def test_rule_rejection_records_actor_timestamp_fields_and_note(self):
        connection = FakeConnection({"id": 27, "status": "proposed"})
        change_rule_status(
            connection,
            27,
            "rejected",
            "alice@example.com",
            "Not supported by the source contract.",
        )

        update = connection.calls[1]
        audit = connection.calls[2]
        self.assertIn("decision_at = CURRENT_TIMESTAMP", update[0])
        self.assertEqual(audit[1]["previous_status"], "proposed")
        self.assertEqual(audit[1]["new_status"], "rejected")
        self.assertEqual(audit[1]["changed_by"], "alice@example.com")
        self.assertEqual(
            audit[1]["decision_note"],
            "Not supported by the source contract.",
        )

    def test_remediation_approval_records_complete_decision(self):
        connection = FakeConnection({"id": 492, "status": "proposed"})
        change_remediation_status(
            connection,
            492,
            "approved",
            "bob@example.com",
            "Verified against the source document.",
        )

        update = connection.calls[1]
        audit = connection.calls[2]
        self.assertIn("approved_at = CURRENT_TIMESTAMP", update[0])
        self.assertEqual(audit[1]["previous_status"], "proposed")
        self.assertEqual(audit[1]["new_status"], "approved")
        self.assertEqual(audit[1]["changed_by"], "bob@example.com")

    def test_blank_steward_identity_is_rejected(self):
        connection = FakeConnection({"id": 27, "status": "proposed"})
        with self.assertRaisesRegex(ValueError, "steward identity"):
            change_rule_status(connection, 27, "rejected", "   ")


if __name__ == "__main__":
    unittest.main()
