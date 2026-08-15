from scripts.generate_reference_rules import build_reference_proposals


def test_reference_rules_are_built_deterministically_from_columns():
    proposals = build_reference_proposals(
        ["customer_id", "city", "state_code", "zip_code", "naics_code"]
    )
    rule_types = [proposal[2]["type"] for proposal in proposals]

    assert rule_types.count("reference_value") == 3
    assert "city_state_zip_reference" in rule_types
    city_rule = next(
        executable
        for _, _, executable in proposals
        if executable["type"] == "city_state_zip_reference"
    )
    assert city_rule["parameters"] == {
        "city_column": "city",
        "state_column": "state_code",
        "zip_column": "zip_code",
        "reference_dataset": "us_zip_codes",
    }


def test_unknown_column_names_do_not_cause_factual_guesses():
    assert build_reference_proposals(["field_a", "field_b"]) == []
