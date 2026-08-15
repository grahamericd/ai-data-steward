import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import engine
from rule_registry import validate_executable_rule
from stewardship_context import get_stewardship_run_id


def find_column(columns, *names):
    by_name = {column.lower(): column for column in columns}
    for name in names:
        if name in by_name:
            return by_name[name]
    return None


def build_reference_proposals(columns):
    """Build conservative proposals from column names, never factual guesses."""

    proposals = []
    state = find_column(columns, "state_code", "state")
    zip_column = find_column(columns, "zip_code", "zip", "postal_code")
    city = find_column(columns, "city", "place_name")
    naics = find_column(columns, "naics_code", "naics")

    if state:
        proposals.append(
            ("COLUMN", [state], {
                "type": "reference_value",
                "parameters": {
                    "reference_dataset": "us_states",
                    "reference_column": "state_code",
                },
            })
        )
    if zip_column:
        proposals.append(
            ("COLUMN", [zip_column], {
                "type": "reference_value",
                "parameters": {
                    "reference_dataset": "us_zip_codes",
                    "reference_column": "zip_code",
                },
            })
        )
    if naics:
        proposals.append(
            ("COLUMN", [naics], {
                "type": "reference_value",
                "parameters": {
                    "reference_dataset": "naics",
                    "reference_column": "naics_code",
                },
            })
        )
    if city and state and zip_column:
        proposals.append(
            ("ROW", [city, state, zip_column], {
                "type": "city_state_zip_reference",
                "parameters": {
                    "city_column": city,
                    "state_column": state,
                    "zip_column": zip_column,
                    "reference_dataset": "us_zip_codes",
                },
            })
        )
    return proposals


def generate_reference_rules(conn, dataset_name):
    dataset = conn.execute(
        text("""
            SELECT dataset_name, raw_schema, raw_table
            FROM metadata.dataset
            WHERE dataset_name = :dataset_name AND active = TRUE
        """),
        {"dataset_name": dataset_name},
    ).mappings().first()
    if dataset is None:
        raise ValueError(f"Dataset not found or inactive: {dataset_name}")

    columns = conn.execute(
        text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = :schema_name AND table_name = :table_name
              AND column_name NOT LIKE '\\_%' ESCAPE '\\'
            ORDER BY ordinal_position
        """),
        {"schema_name": dataset["raw_schema"], "table_name": dataset["raw_table"]},
    ).scalars().all()

    inserted = 0
    for scope, target_columns, executable_rule in build_reference_proposals(columns):
        valid, reason, executable_rule = validate_executable_rule(
            executable_rule, expected_scope=scope
        )
        if not valid:
            raise ValueError(reason)
        duplicate = conn.execute(
            text("""
                SELECT 1 FROM dq.rule
                WHERE dataset_name = :dataset_name
                  AND rule_definition -> 'executable_rule' = CAST(:rule AS jsonb)
                  AND status NOT IN ('rejected', 'retired')
                LIMIT 1
            """),
            {"dataset_name": dataset_name, "rule": json.dumps(executable_rule)},
        ).first()
        if duplicate:
            continue

        definition = {
            "business_definition": "Validate against registered authoritative reference data.",
            "evidence": "Deterministically mapped from conventional column names; no LLM used.",
            "target_columns": target_columns,
            "executable_rule": executable_rule,
        }
        conn.execute(
            text("""
                INSERT INTO dq.rule
                (
                    dataset_name, column_name, rule_type, rule_definition,
                    status, rule_scope, target_columns, generated_by,
                    prompt_version, stewardship_run_id
                )
                VALUES
                (
                    :dataset_name, :column_name, :rule_type,
                    CAST(:definition AS jsonb), 'proposed', :scope,
                    CAST(:target_columns AS jsonb), 'deterministic_reference',
                    'reference-rule-v1', :stewardship_run_id
                )
            """),
            {
                "dataset_name": dataset_name,
                "column_name": target_columns[0] if scope == "COLUMN" else None,
                "rule_type": executable_rule["type"],
                "definition": json.dumps(definition),
                "scope": scope,
                "target_columns": json.dumps(target_columns),
                "stewardship_run_id": get_stewardship_run_id(),
            },
        )
        inserted += 1
    return inserted


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset_name")
    args = parser.parse_args()
    with engine.begin() as conn:
        inserted = generate_reference_rules(conn, args.dataset_name)
    print(f"Reference rules proposed: {inserted}")


if __name__ == "__main__":
    main()
