import argparse
import json
import re
import sys
from pathlib import Path

from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import engine
from rule_registry import canonical_rule_type, extract_executable_rule
from scripts.load_dataset import quote_identifier, validate_identifier
from stewardship_context import get_stewardship_run_id

STATE_CODES = (
    "AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|IA|ID|IL|IN|KS|KY|LA|MA|MD|ME|MI|MN|MO|MS|MT|"
    "NC|ND|NE|NH|NJ|NM|NV|NY|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VA|VT|WA|WI|WV|WY"
)
CITY_STATE_PATTERN = re.compile(rf"^(?P<city>.+?)[,\s]+(?P<state>{STATE_CODES})\s*$")
CITY_ZIP_PATTERN = re.compile(r"^(?P<city>.+?)\s+(?P<zip>\d{5}(?:-\d{4})?)\s*$")
STATE_ZIP_PATTERN = re.compile(
    rf"^(?P<state>{STATE_CODES})[,\s-]+(?P<zip>\d{{5}}(?:-\d{{4}})?)\s*$"
)


def is_blank(value):
    return value is None or str(value).strip() == ""


def find_column(row, *candidates):
    columns = {str(column).lower(): column for column in row}
    for candidate in candidates:
        if candidate and candidate.lower() in columns:
            return columns[candidate.lower()]
    return None


def deterministic_location_suggestion(rule_type, column_name, parameters, row):
    """Return a lossless location correction, or None when it is unsafe."""

    if rule_type == "city_contains_state_or_zip":
        city_column = find_column(
            row, parameters.get("city_column"), column_name, "city"
        )
        if not city_column or is_blank(row.get(city_column)):
            return None

        city = str(row[city_column]).strip()
        state_column = find_column(row, "state", "state_code")
        zip_column = find_column(row, "zip", "zip_code", "postal_code")

        state_match = CITY_STATE_PATTERN.fullmatch(city)
        if state_match and state_column and is_blank(row.get(state_column)):
            return {
                "issue_type": "city_contains_state",
                "original_values": {
                    city_column: row.get(city_column),
                    state_column: row.get(state_column),
                },
                "suggested_values": {
                    city_column: state_match.group("city").strip().rstrip(","),
                    state_column: state_match.group("state"),
                },
                "confidence_score": 0.95,
                "generation_method": "deterministic",
            }

        zip_match = CITY_ZIP_PATTERN.fullmatch(city)
        if zip_match and zip_column and is_blank(row.get(zip_column)):
            return {
                "issue_type": "city_contains_zip",
                "original_values": {
                    city_column: row.get(city_column),
                    zip_column: row.get(zip_column),
                },
                "suggested_values": {
                    city_column: zip_match.group("city").strip().rstrip(","),
                    zip_column: zip_match.group("zip"),
                },
                "confidence_score": 0.95,
                "generation_method": "deterministic",
            }

    if rule_type == "state_field_contains_zip":
        state_column = find_column(
            row, parameters.get("state_column"), column_name, "state", "state_code"
        )
        zip_column = find_column(row, "zip", "zip_code", "postal_code")
        if not state_column or not zip_column or not is_blank(row.get(zip_column)):
            return None

        match = STATE_ZIP_PATTERN.fullmatch(str(row.get(state_column) or "").strip())
        if match:
            return {
                "issue_type": "state_contains_zip",
                "original_values": {
                    state_column: row.get(state_column),
                    zip_column: row.get(zip_column),
                },
                "suggested_values": {
                    state_column: match.group("state"),
                    zip_column: match.group("zip"),
                },
                "confidence_score": 0.95,
                "generation_method": "deterministic",
            }

    # LLM-assisted remediation is a distinct future path. Without evidence
    # that validates a proposed replacement, skipping is safer than guessing.
    return None


def get_dataset(conn, dataset_name):
    return conn.execute(
        text("""
            SELECT * FROM metadata.dataset
            WHERE dataset_name = :dataset_name AND active = TRUE
        """),
        {"dataset_name": dataset_name},
    ).mappings().first()


def get_unremediated_failures(conn, dataset_name):
    return conn.execute(
        text("""
            SELECT
                fr.id AS failed_record_id, fr.result_id, fr.rule_id,
                fr.source_row_identifier, ru.column_name,
                ru.rule_type, ru.rule_definition
            FROM dq.failed_record fr
            INNER JOIN dq.result result ON result.id = fr.result_id
            INNER JOIN dq.rule ru ON ru.id = fr.rule_id
            LEFT JOIN dq.remediation_suggestion remediation
                ON remediation.failed_record_id = fr.id
            WHERE fr.dataset_name = :dataset_name
              AND result.result_status = 'FAIL'
              AND remediation.id IS NULL
            ORDER BY fr.id
        """),
        {"dataset_name": dataset_name},
    ).mappings().all()


def load_source_row(conn, dataset, source_row_identifier):
    schema = validate_identifier(dataset["raw_schema"] or "raw", "raw schema")
    table = validate_identifier(dataset["raw_table"], "raw table")
    primary_key = validate_identifier(dataset["primary_key"], "primary key")
    return conn.execute(
        text(
            f"SELECT * FROM {quote_identifier(schema)}.{quote_identifier(table)} "
            f"WHERE {quote_identifier(primary_key)} = :identifier"
        ),
        {"identifier": source_row_identifier},
    ).mappings().first()


def insert_suggestion(conn, dataset, failure, suggestion):
    source_table = f"{dataset['raw_schema'] or 'raw'}.{dataset['raw_table']}"
    return conn.execute(
        text("""
            INSERT INTO dq.remediation_suggestion
            (
                dataset_name, source_table, rule_id, result_id,
                failed_record_id, source_row_identifier, issue_type,
                remediation_type, generation_method, original_values,
                suggested_values, confidence_score, status
                ,stewardship_run_id
            )
            VALUES
            (
                :dataset_name, :source_table, :rule_id, :result_id,
                :failed_record_id, :source_row_identifier, :issue_type,
                'suggested', :generation_method,
                CAST(:original_values AS jsonb),
                CAST(:suggested_values AS jsonb),
                :confidence_score, 'proposed'
                ,:stewardship_run_id
            )
            ON CONFLICT (failed_record_id) DO NOTHING
        """),
        {
            "dataset_name": dataset["dataset_name"],
            "source_table": source_table,
            "rule_id": failure["rule_id"],
            "result_id": failure["result_id"],
            "failed_record_id": failure["failed_record_id"],
            "source_row_identifier": failure["source_row_identifier"],
            "issue_type": suggestion["issue_type"],
            "generation_method": suggestion["generation_method"],
            "original_values": json.dumps(suggestion["original_values"]),
            "suggested_values": json.dumps(suggestion["suggested_values"]),
            "confidence_score": suggestion["confidence_score"],
            "stewardship_run_id": get_stewardship_run_id(),
        },
    )


def generate_for_dataset(conn, dataset_name):
    dataset = get_dataset(conn, dataset_name)
    if not dataset:
        raise ValueError(f"Dataset not found or inactive: {dataset_name}")
    dataset = dict(dataset)
    if not dataset.get("primary_key"):
        raise ValueError(
            f"Dataset '{dataset_name}' needs metadata.dataset.primary_key "
            "before row-level remediations can be generated."
        )

    failures = get_unremediated_failures(conn, dataset_name)
    inserted = 0
    skipped = 0
    for failure in failures:
        try:
            failure = dict(failure)
            row = load_source_row(conn, dataset, failure["source_row_identifier"])
            if not row:
                skipped += 1
                continue

            executable_rule = extract_executable_rule(failure["rule_definition"])
            suggestion = deterministic_location_suggestion(
                canonical_rule_type(executable_rule["type"]),
                failure["column_name"],
                executable_rule.get("parameters", {}),
                dict(row),
            )
            if suggestion is None:
                skipped += 1
                continue

            result = insert_suggestion(conn, dataset, failure, suggestion)
            inserted += max(result.rowcount, 0)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            # Historical malformed failures must not prevent safe suggestions
            # for the remaining records.
            skipped += 1

    return {"failures": len(failures), "inserted": inserted, "skipped": skipped}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset_name")
    args = parser.parse_args()
    with engine.begin() as conn:
        summary = generate_for_dataset(conn, args.dataset_name)

    print(f"Failed records considered: {summary['failures']}")
    print(f"Remediation suggestions inserted: {summary['inserted']}")
    print(f"Safely skipped: {summary['skipped']}")


if __name__ == "__main__":
    main()
