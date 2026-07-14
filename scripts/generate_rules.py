import os
import json
import subprocess
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
import sys

# load_dotenv(os.path.expanduser("~/.datalab.env"))

# engine = create_engine(
    # f"postgresql+psycopg2://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}/{os.getenv('DB_NAME')}"
# )

engine = create_engine(
    "postgresql://egraham@localhost/florida_data_lab",
    connect_args={
        "password": "P@ssw0rd12345"
    }
)


if len(sys.argv) != 2:
    print("Usage: python generate_rules.py <dataset_name>")
    sys.exit(1)

DATASET_NAME = sys.argv[1]

#DATASET_NAME = "corporate_data"
MODEL = "llama3.2"

def call_ollama(prompt):
    result = subprocess.run(
        ["ollama", "run", MODEL],
        input=prompt,
        capture_output=True,
        text=True
    )
    return result.stdout.strip()

def build_prompt(profile):
    return f"""
You are an enterprise data quality steward.

Analyze this column profile and propose ONE executable data quality rule.

Return ONLY valid JSON. Do not include markdown.

Dataset: {profile['dataset_name']}
Column: {profile['column_name']}

Profile:
- Row count: {profile['row_count']}
- Null count: {profile['null_count']}
- Null percent: {profile['null_percent']}
- Distinct count: {profile['distinct_count']}
- Inferred type: {profile['inferred_type']}
- Sample values: {profile['sample_values']}
- Min value: {profile['min_value']}
- Max value: {profile['max_value']}

Required JSON structure:
{{
  "business_definition": "",
  "expected_data_type": "",
  "quality_rules": [],
  "cleansing_actions": [],
  "executable_rule": {{
    "type": "",
    "parameters": {{}}
  }}
}}

Use rule types such as:
- allowed_values
- not_null
- max_length
- date_format
- city_ends_with_state_or_zip
- state_field_contains_zip
"""

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
            response = call_ollama(prompt)

            try:
                rule_json = json.loads(response)
                rule_json = apply_rule_guardrails(profile, rule_json)
            except json.JSONDecodeError:
                print(f"Skipped {profile['column_name']}: invalid JSON")
                print(response)
                continue

            conn.execute(
                text("""
                    INSERT INTO dq.rule
                    (
                        dataset_name,
                        column_name,
                        rule_type,
                        rule_definition,
                        status
                    )
                    VALUES
                    (
                        :dataset_name,
                        :column_name,
                        'llm_generated',
                        CAST(:rule_definition AS jsonb),
                        :status
                    )
                """),
                {
                    "dataset_name": profile["dataset_name"],
                    "column_name": profile["column_name"],
                    "rule_definition": json.dumps(rule_json),
                    "status": "guardrail_rejected" if rule_json.get("guardrail_applied") else "proposed"
                }
            )

            inserted += 1
            print(f"Inserted rule for {profile['column_name']}")

        print(f"Inserted {inserted} proposed rules.")

if __name__ == "__main__":
    main()