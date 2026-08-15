#import os
import json
#import subprocess
from sqlalchemy import text
#from sqlalchemy.engine import URL
from pathlib import Path

import sys
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
    
from config import LLM_MODEL, LLM_PROVIDER, RAW_DATA_DIR, engine
from llm_client import LLMError, generate_json

from rule_registry import (
    build_llm_rule_catalog,
    validate_executable_rule,
)
from stewardship_context import get_stewardship_run_id

if len(sys.argv) != 2:
    print("Usage: python generate_rules.py <dataset_name>")
    sys.exit(1)

DATASET_NAME = sys.argv[1]

#DATASET_NAME = "corporate_data"
#MODEL = "llama3.2"

# def call_ollama(prompt):
    # result = subprocess.run(
        # ["ollama", "run", MODEL],
        # input=prompt,
        # capture_output=True,
        # text=True
    # )
    # return result.stdout.strip()


def build_prompt(profile):
    
    supported_rules = build_llm_rule_catalog(
    "COLUMN"
)
    return f"""
You are a senior enterprise data steward responsible for creating reusable,
machine-executable data quality rules.

Your objective is to create ONE high-value data quality rule that improves
the quality, consistency, completeness, or validity of this column.

Instructions:

1. Produce exactly one rule.
2. The rule must be deterministic and executable by software.
3. Use only evidence available in the column name and profile.
4. Do not invent business requirements.
5. Prefer rules that remain useful for future records.
6. Do not create an allowed_values rule for names, addresses, cities,
   descriptions, identifiers, or other high-cardinality free-text columns.
7. Use allowed_values only when the observed domain is clearly categorical,
   stable, and small.
8. Do not require a column to be non-null merely because the current profile
   contains no null values.
9. Do not infer a numeric range solely from the observed minimum and maximum.
10. Do not propose truncation or another cleansing action that could destroy
    valid information.
11. If there is insufficient evidence for a safe and useful rule, return
    executable_rule type "none".
12. Do not invent rule types that are not listed below.


Dataset:
{profile['dataset_name']}

Column:
{profile['column_name']}

Observed profile:
- Row count: {profile['row_count']}
- Null count: {profile['null_count']}
- Null percent: {profile['null_percent']}
- Distinct count: {profile['distinct_count']}
- Inferred type: {profile['inferred_type']}
- Minimum value: {profile['min_value']}
- Maximum value: {profile['max_value']}
- Sample values: {profile['sample_values']}

Supported executable COLUMN rule types:
{supported_rules}



Return exactly this JSON structure:

{{
  "business_definition": "",
  "expected_data_type": "",
  "quality_rules": [],
  "cleansing_actions": [],
  "confidence_score": 0.0,
  "evidence": "",
  "executable_rule": {{
    "type": "",
    "parameters": {{}}
  }}
}}

Rules for the response:

- Return only valid JSON.
- Do not use markdown or code fences.
- Use double quotes for every JSON key and string.
- Do not include comments.
- confidence_score must be between 0.0 and 1.0.
- evidence must briefly identify which profile facts support the rule.
"""


# def build_prompt(profile):
    # return f"""
# You are an enterprise data quality steward.

# Analyze this column profile and propose ONE executable data quality rule.

# Return ONLY valid JSON. Do not include markdown.

# Dataset: {profile['dataset_name']}
# Column: {profile['column_name']}

# Profile:
# - Row count: {profile['row_count']}
# - Null count: {profile['null_count']}
# - Null percent: {profile['null_percent']}
# - Distinct count: {profile['distinct_count']}
# - Inferred type: {profile['inferred_type']}
# - Sample values: {profile['sample_values']}
# - Min value: {profile['min_value']}
# - Max value: {profile['max_value']}

# Required JSON structure:
# {{
  # "business_definition": "",
  # "expected_data_type": "",
  # "quality_rules": [],
  # "cleansing_actions": [],
  # "executable_rule": {{
    # "type": "",
    # "parameters": {{}}
  # }}
# }}

# Use rule types such as:
# - allowed_values
# - not_null
# - max_length
# - date_format
# - city_ends_with_state_or_zip
# - state_field_contains_zip
#"""

def apply_rule_guardrails(profile, rule_json):
    column_name = profile["column_name"].lower()
    distinct_count = profile["distinct_count"] or 0
    row_count = profile["row_count"] or 0

    executable_rule = rule_json.get("executable_rule", {})
    rule_type = executable_rule.get("type", "")

    free_text_patterns = [
        "_name",
        "_address",
        "_city",
        "address",
        "name",
        "city",
    ]

    is_free_text_column = any(pattern in column_name for pattern in free_text_patterns)

    if rule_type == "allowed_values" and is_free_text_column:
        if distinct_count > 25:
            rule_json["guardrail_applied"] = True
            rule_json["guardrail_reason"] = (
                "Rejected allowed_values rule for likely free-text column "
                f"with {distinct_count} distinct values."
            )
            rule_json["executable_rule"] = {
                "type": "not_executable",
                "parameters": {}
            }

    return rule_json

def main():
    with engine.begin() as conn:
        profiles = conn.execute(
            text("""
                SELECT *
                FROM metadata.column_profile
                WHERE dataset_name = :dataset_name
                ORDER BY column_name
            """),
            {"dataset_name": DATASET_NAME}
        ).mappings().all()

        inserted = 0

        for profile in profiles:
            prompt = build_prompt(profile)

            try:
                rule_json = generate_json(prompt)
                
                executable_rule = rule_json.get(
                    "executable_rule",
                    {}
                )

                if executable_rule.get("type") == "none":
                    print(
                        f"Skipped {profile['column_name']}: "
                        "LLM found no defensible rule."
                    )
                    continue

                valid, reason, cleaned_rule = (
                    validate_executable_rule(
                        executable_rule,
                        expected_scope="COLUMN",
                    )
                )

                if not valid:
                    print(
                        f"Skipped {profile['column_name']}: "
                        f"registry validation failed: {reason}"
                    )
                    continue

                rule_json[
                    "executable_rule"
                ] = cleaned_rule

                rule_json = apply_rule_guardrails(
                    profile,
                    rule_json
                )

            except LLMError as exc:
                print(
                    f"Skipped {profile['column_name']}: "
                    f"{exc}"
                )
                continue
            # prompt = build_prompt(profile)
            # response = call_ollama(prompt)

            # try:
                # rule_json = json.loads(response)
                # rule_json = apply_rule_guardrails(profile, rule_json)
            # except json.JSONDecodeError:
                # print(f"Skipped {profile['column_name']}: invalid JSON")
                # print(response)
                # continue

            conn.execute(
                text("""
                    INSERT INTO dq.rule
                    (
                        dataset_name,
                        column_name,
                        rule_type,
                        rule_definition,
                        status,
                        generated_by,
                        llm_provider,
                        llm_model,
                        prompt_version
                        ,stewardship_run_id
                    )
                    VALUES
                    (
                        :dataset_name,
                        :column_name,
                        'llm_generated',
                        CAST(:rule_definition AS jsonb),
                        :status,
                        'llm',
                        :llm_provider,
                        :llm_model,
                        'column-rule-v1'
                        ,:stewardship_run_id
                    )
                """),
                {
                    "dataset_name": profile["dataset_name"],
                    "column_name": profile["column_name"],
                    "rule_definition": json.dumps(rule_json),
                    "status": "guardrail_rejected" if rule_json.get("guardrail_applied") else "proposed",
                    "llm_provider": LLM_PROVIDER,
                    "llm_model": LLM_MODEL,
                    "stewardship_run_id": get_stewardship_run_id(),
                }
            )

            inserted += 1
            print(f"Inserted rule for {profile['column_name']}")

        print(f"Inserted {inserted} proposed rules.")

if __name__ == "__main__":
    main()
