import pandas as pd

from scripts.generate_multicolumn_rules import (
    MIN_OBSERVED_SUPPORT,
    analyze_candidate,
    candidate_limit,
    discover_candidates,
    screen_candidates,
    validate_candidate_response,
)


def profile(column, inferred_type="text", distinct_count=4, null_percent=0):
    return {
        "column_name": column,
        "row_count": 40,
        "null_count": 0,
        "null_percent": null_percent,
        "distinct_count": distinct_count,
        "inferred_type": inferred_type,
        "sample_values": [],
        "min_value": None,
        "max_value": None,
    }


def candidate(candidate_type, left, right):
    rule_types = {
        "date_relationship": {"column_comparison"},
        "status_date_relationship": {"conditional_required"},
        "identifier_relationship": {"at_least_one_present"},
        "conditional_completeness": {"conditional_required"},
        "repeated_structure_completeness": {"conditional_required"},
    }
    return {
        "candidate_type": candidate_type,
        "left_column": left,
        "right_column": right,
        "score": 80,
        "reason": "test",
        "allowed_rule_types": rule_types[candidate_type],
    }


def test_support_analysis_covers_requested_relationship_families():
    rows = 40
    df = pd.DataFrame({
        "cancellation_status": ["cancelled"] * 30 + ["active"] * 10,
        "cancellation_date": ["2026-01-01"] * 30 + [None] * 10,
        "license_id": [None] * 20 + [f"L{i}" for i in range(20)],
        "license_number": [f"N{i}" for i in range(20)] + [None] * 20,
        "contact_type": ["email"] * 30 + ["none"] * 10,
        "contact_email": ["a@example.com"] * 30 + [None] * 10,
        "owner_1_type": ["person"] * rows,
        "owner_1_name": ["Ada"] * rows,
    })

    cases = [
        ("status_date_relationship", "cancellation_status", "cancellation_date"),
        ("identifier_relationship", "license_id", "license_number"),
        ("conditional_completeness", "contact_type", "contact_email"),
        ("repeated_structure_completeness", "owner_1_type", "owner_1_name"),
    ]
    for family, left, right in cases:
        evidence = analyze_candidate(df, candidate(family, left, right))
        assert evidence is not None
        assert evidence["support"] >= MIN_OBSERVED_SUPPORT
        assert evidence["comparable_rows"] >= 25


def test_discovery_includes_conditional_and_repeated_structures():
    profiles = [
        profile("contact_type", distinct_count=2),
        profile("contact_email", distinct_count=35, null_percent=25),
        profile("owner_1_type", distinct_count=2),
        profile("owner_1_name", distinct_count=35),
    ]
    families = {item["candidate_type"] for item in discover_candidates(profiles)}
    assert "conditional_completeness" in families
    assert "repeated_structure_completeness" in families


def test_screening_counters_and_provider_limits_are_accurate():
    df = pd.DataFrame({"a": [None] * 40, "b": [None] * 40})
    candidates = [candidate("identifier_relationship", "a", "b") for _ in range(5)]
    screened, diagnostics = screen_candidates(df, candidates, "ollama")

    assert screened == []
    assert diagnostics == {
        "discovered": 5,
        "insufficient_data": 0,
        "low_support": 5,
        "supported": 0,
        "selected_for_llm": 0,
        "capacity_skipped": 0,
    }
    assert candidate_limit("ollama") < candidate_limit("openai")


def test_semantic_review_must_preserve_empirical_condition_and_is_calibrated():
    item = candidate("status_date_relationship", "status", "closed_date")
    item["empirical_evidence"] = {
        "condition_column": "status",
        "condition_operator": "==",
        "condition_value": "closed",
        "required_column": "closed_date",
        "comparable_rows": 100,
        "passed_rows": 97,
        "failed_rows": 3,
        "support": 0.97,
    }
    profiles = {
        "status": profile("status"),
        "closed_date": profile("closed_date", "date"),
    }
    response = {
        "accepted": True,
        "confidence_score": 0.99,
        "business_definition": "Closed records require a close date.",
        "evidence": "Observed relationship.",
        "executable_rule": {
            "type": "conditional_required",
            "parameters": {
                "condition_column": "status",
                "condition_operator": "==",
                "condition_value": "closed",
                "required_column": "closed_date",
            },
        },
    }

    valid, _, rule = validate_candidate_response(response, item, profiles)
    assert valid
    assert rule["model_confidence_score"] == 0.99
    assert rule["confidence_score"] == 0.97

    response["executable_rule"]["parameters"]["condition_value"] = "active"
    valid, reason, _ = validate_candidate_response(response, item, profiles)
    assert not valid
    assert "does not match empirical evidence" in reason
