import pytest

from rule_registry import validate_executable_rule


@pytest.mark.parametrize(
    ("rule", "message"),
    [
        ({"type": "unknown", "parameters": {}}, "Unsupported rule type"),
        ({"type": "regex", "parameters": {}}, "missing required parameter"),
        (
            {"type": "numeric_range", "parameters": {"min": 10, "max": 1}},
            "Range min",
        ),
        (
            {
                "type": "column_comparison",
                "parameters": {
                    "left_column": "a",
                    "operator": "contains",
                    "right_column": "b",
                },
            },
            "not allowed",
        ),
    ],
)
def test_registry_rejects_invalid_contracts(rule, message):
    valid, reason, cleaned = validate_executable_rule(rule)
    assert not valid
    assert message in reason
    assert cleaned is None
