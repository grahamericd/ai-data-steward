import os
import json
import re
import pandas as pd
from pathlib import Path
from sqlalchemy import text
from sqlalchemy.engine import URL
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
    
from config import RAW_DATA_DIR, engine


if len(sys.argv) != 2:
    print("Usage: python evaluate_rules.py <dataset_name>")
    sys.exit(1)

DATASET_NAME = sys.argv[1]


def percent_to_number(value):
    if value is None:
        return None
    value = str(value).strip()
    if value == "":
        return None
    value = value.replace("%", "")
    try:
        return float(value)
    except ValueError:
        return None


def evaluate_percentage_range(df, column_name, min_value=0, max_value=100):
    values = df[column_name].apply(percent_to_number)
    failed_rows = df[
        values.isna() |
        (values < min_value) |
        (values > max_value)
    ]
    return {
        "rows_checked": len(df),
        "failed_count": len(failed_rows),
        "sample_failures": failed_rows.head(10).to_dict(orient="records")
    }

def evaluate_allowed_values(df, column_name, allowed_values):
    values = df[column_name].astype(str).str.strip()
    failed_rows = df[
        ~values.isin(allowed_values)
    ]
    return {
        "rows_checked": len(df),
        "failed_count": len(failed_rows),
        "allowed_values": allowed_values,
        "sample_failures": failed_rows.head(10).to_dict(orient="records")
    }

def evaluate_city_contains_state_or_zip(df, city_column):
    pattern = r"\b[A-Z]{2}\b|\b\d{5}(-\d{4})?\b"

    values = df[city_column].fillna("").astype(str).str.strip().str.upper()

    failed_rows = df[values.str.contains(pattern, regex=True, na=False)]

    return {
        "rows_checked": len(df),
        "failed_count": len(failed_rows),
        "sample_failures": failed_rows.head(10).to_dict(orient="records")
    }


def evaluate_state_field_contains_zip(df, state_column):
    values = df[state_column].fillna("").astype(str).str.strip().str.upper()

    failed_rows = df[
        values.str.match(r"^\d{5}(-\d{4})?$", na=False) |
        values.str.match(r"^[A-Z]{2}\d{5}", na=False)
    ]

    return {
        "rows_checked": len(df),
        "failed_count": len(failed_rows),
        "sample_failures": failed_rows.head(10).to_dict(orient="records")
    }


def infer_rule_action(rule_definition):

    rule_text = json.dumps(rule_definition).lower()
    
    if "executable_rule" in rule_definition:
        return rule_definition["executable_rule"]

    if "allowed_values" in rule_text or "valid values" in rule_text:
        if "a" in rule_text and "i" in rule_text:
            return {
                "type": "allowed_values",
                "values": ["A", "I"]
            }

    if "percentage" in rule_text or "%" in rule_text:
        if "0" in rule_text and "100" in rule_text:
            return {
                "type": "percentage_range",
                "min": 0,
                "max": 100
            }

    return {
        "type": "not_executable_yet"
    }


def main():
    with engine.begin() as conn:
        rules = conn.execute(
            text("""
                SELECT
                    id,
                    dataset_name,
                    column_name,
                    rule_type,
                    rule_definition
                FROM dq.rule
                WHERE status = 'approved'
                AND dataset_name = :dataset_name
            """),
            {"dataset_name": DATASET_NAME}
        ).mappings().all()

        if not rules:
            print("No approved rules found.")
            return

        for rule in rules:
            rule_id = rule["id"]
            dataset_name = rule["dataset_name"]
            column_name = rule["column_name"]
            rule_definition = rule["rule_definition"]

            print(f"Evaluating rule {rule_id}: {dataset_name}.{column_name}")

            df = pd.read_sql(
                f"SELECT * FROM raw.{dataset_name}",
                engine
            )

            action = infer_rule_action(rule_definition)

            if action["type"] == "percentage_range":
                result_details = evaluate_percentage_range(
                    df,
                    column_name,
                    min_value=action["min"],
                    max_value=action["max"]
                )
                
            elif action["type"] == "allowed_values":
                result_details = evaluate_allowed_values(
                    df,
                    column_name,
                    #allowed_values=action["values"]
                    allowed_values=action.get("values") or action.get("parameters", {}).get("values")
                )

            elif action["type"] == "city_contains_state_or_zip":
                result_details = evaluate_city_contains_state_or_zip(
                    df,
                    city_column=action["city_column"]
                )

            elif action["type"] == "state_field_contains_zip":
                result_details = evaluate_state_field_contains_zip(
                    df,
                    state_column=action["state_column"]
                )
            else:
                result_details = {
                    "message": "Rule is not executable",
                    "rule_definition": rule_definition
                }
            #else:
            #    result_status = "SKIPPED"
            #    result_details = {
            #        "message": "Rule is not executable yet.",
            #        "rule_definition": rule_definition
            #    }
                
            #result_status = (
            #        "PASS"
            #        if result_details["failed_count"] == 0
            #        else "FAIL"
            #    )
            if "failed_count" in result_details:
                result_status = (
                    "PASS"
                    if result_details["failed_count"] == 0
                    else "FAIL"
                )
                
            else: 
                result_status = "SKIPPED"
                result_details["failed_count"] = 0
                

            conn.execute(
                text("""
                    INSERT INTO dq.result
                    (
                        dataset_name,
                        rule_id,
                        result_status,
                        failed_count,
                        details
                    )
                    VALUES
                    (
                        :dataset_name,
                        :rule_id,
                        :result_status,
                        :failed_count,
                        CAST(:details AS jsonb)
                    )
                """),
                {
                    "dataset_name": dataset_name,
                    "rule_id": rule_id,
                    "result_status": result_status,
                    "failed_count": result_details.get("failed_count", 0),
                    "details": json.dumps(result_details)
                }
            )

            print(
                f"Result: {result_status} | "
                f"Failures: {result_details.get('failed_count', 0)}"
            )


if __name__ == "__main__":
    main()
