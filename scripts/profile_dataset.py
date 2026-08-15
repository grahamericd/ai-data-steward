import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import engine
from scripts.load_dataset import quote_identifier, validate_identifier

SAMPLE_LIMIT = 10
SAMPLE_SCAN_LIMIT = 100
INTERNAL_COLUMNS = {"_source_file", "_ingested_at", "_load_run_id"}


def infer_type_from_sample(sample_values, database_type=None):
    """Use database metadata first and bounded samples for text semantics."""

    normalized_type = str(database_type or "").lower()
    if normalized_type in {"smallint", "integer", "bigint"}:
        return "integer"
    if normalized_type in {"numeric", "decimal", "real", "double precision"}:
        return "numeric"
    if normalized_type in {"date", "timestamp", "timestamp with time zone", "timestamp without time zone"}:
        return "date"
    if normalized_type == "boolean":
        return "boolean"

    sample = pd.Series(sample_values, dtype="string").dropna().str.strip()
    if sample.empty:
        return "unknown"
    if sample.str.match(r"^\$[\d,]+(?:\.\d+)?$").all():
        return "currency"
    if sample.str.match(r"^\d+(?:\.\d+)?%$").all():
        return "percentage"
    if sample.str.match(r"^\d+$").all():
        return "year" if sample.str.match(r"^\d{4}$").all() else "integer"
    return "text"


def get_dataset_config(conn, dataset_name):
    return conn.execute(
        text("""
            SELECT * FROM metadata.dataset
            WHERE dataset_name = :dataset_name AND active = TRUE
        """),
        {"dataset_name": dataset_name},
    ).mappings().first()


def get_table_columns(conn, schema_name, table_name):
    rows = conn.execute(
        text("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = :schema_name
              AND table_name = :table_name
            ORDER BY ordinal_position
        """),
        {"schema_name": schema_name, "table_name": table_name},
    ).mappings().all()
    return [dict(row) for row in rows if row["column_name"] not in INTERNAL_COLUMNS]


def profile_sql_column(
    conn,
    schema_name,
    table_name,
    column_name,
    database_type,
    row_count,
    sample_limit=SAMPLE_LIMIT,
):
    """Profile one column without transferring the column to Python."""

    schema = quote_identifier(validate_identifier(schema_name, "source schema"))
    table = quote_identifier(validate_identifier(table_name, "source table"))
    column = quote_identifier(validate_identifier(column_name, "source column"))

    aggregate = conn.execute(
        text(f"""
            SELECT
                COUNT(*) FILTER (WHERE {column} IS NULL) AS null_count,
                COUNT(DISTINCT {column}::text)
                    FILTER (WHERE {column} IS NOT NULL) AS distinct_count,
                MIN({column}::text) FILTER (WHERE {column} IS NOT NULL) AS min_value,
                MAX({column}::text) FILTER (WHERE {column} IS NOT NULL) AS max_value
            FROM {schema}.{table}
        """)
    ).mappings().one()

    sampled_values = conn.execute(
        text(f"""
            SELECT {column}::text AS sample_value
            FROM {schema}.{table}
            WHERE {column} IS NOT NULL
            LIMIT :sample_scan_limit
        """),
        {"sample_scan_limit": max(int(sample_limit), SAMPLE_SCAN_LIMIT)},
    ).scalars().all()
    sample_values = list(dict.fromkeys(sampled_values))[:sample_limit]

    null_count = int(aggregate["null_count"] or 0)
    return {
        "row_count": int(row_count),
        "null_count": null_count,
        "null_percent": (
            float(null_count / row_count * 100) if row_count else 0.0
        ),
        "distinct_count": int(aggregate["distinct_count"] or 0),
        "sample_values": [str(value).strip() for value in sample_values],
        "inferred_type": infer_type_from_sample(sample_values, database_type),
        "min_value": aggregate["min_value"],
        "max_value": aggregate["max_value"],
    }


def collect_sql_profiles(conn, dataset_name):
    dataset = get_dataset_config(conn, dataset_name)
    if dataset is None:
        raise ValueError(f"Dataset not found: {dataset_name}")

    schema_name = validate_identifier(dataset["raw_schema"] or "raw", "source schema")
    table_name = validate_identifier(dataset["raw_table"], "source table")
    qualified_table = f"{quote_identifier(schema_name)}.{quote_identifier(table_name)}"
    columns = get_table_columns(conn, schema_name, table_name)

    aggregate_expressions = ["COUNT(*) AS row_count"]
    for index, column_info in enumerate(columns):
        column = quote_identifier(
            validate_identifier(column_info["column_name"], "source column")
        )
        aggregate_expressions.extend(
            [
                f"COUNT(*) FILTER (WHERE {column} IS NULL) AS metric_{index}_null",
                f"COUNT(DISTINCT {column}::text) FILTER (WHERE {column} IS NOT NULL) AS metric_{index}_distinct",
                f"MIN({column}::text) FILTER (WHERE {column} IS NOT NULL) AS metric_{index}_min",
                f"MAX({column}::text) FILTER (WHERE {column} IS NOT NULL) AS metric_{index}_max",
            ]
        )

    aggregates = conn.execute(
        text(
            "SELECT\n    "
            + ",\n    ".join(aggregate_expressions)
            + f"\nFROM {qualified_table}"
        )
    ).mappings().one()
    row_count = int(aggregates["row_count"] or 0)

    profiles = []
    for index, column_info in enumerate(columns):
        column_name = column_info["column_name"]
        column = quote_identifier(column_name)
        sampled_values = conn.execute(
            text(f"""
                SELECT {column}::text AS sample_value
                FROM {qualified_table}
                WHERE {column} IS NOT NULL
                LIMIT :sample_scan_limit
            """),
            {"sample_scan_limit": SAMPLE_SCAN_LIMIT},
        ).scalars().all()
        sample_values = list(dict.fromkeys(sampled_values))[:SAMPLE_LIMIT]
        null_count = int(aggregates[f"metric_{index}_null"] or 0)
        profiles.append(
            {
                "column_name": column_name,
                "row_count": row_count,
                "null_count": null_count,
                "null_percent": float(null_count / row_count * 100) if row_count else 0.0,
                "distinct_count": int(aggregates[f"metric_{index}_distinct"] or 0),
                "sample_values": [str(value).strip() for value in sample_values],
                "inferred_type": infer_type_from_sample(
                    sample_values, column_info["data_type"]
                ),
                "min_value": aggregates[f"metric_{index}_min"],
                "max_value": aggregates[f"metric_{index}_max"],
            }
        )
    return profiles


def persist_profiles(conn, dataset_name, profiles):
    conn.execute(
        text("DELETE FROM metadata.column_profile WHERE dataset_name = :dataset_name"),
        {"dataset_name": dataset_name},
    )
    for profile in profiles:
        conn.execute(
            text("""
                INSERT INTO metadata.column_profile
                (
                    dataset_name, column_name, row_count, null_count,
                    null_percent, distinct_count, sample_values,
                    inferred_type, min_value, max_value
                )
                VALUES
                (
                    :dataset_name, :column_name, :row_count, :null_count,
                    :null_percent, :distinct_count, CAST(:sample_values AS jsonb),
                    :inferred_type, :min_value, :max_value
                )
            """),
            {
                "dataset_name": dataset_name,
                **profile,
                "sample_values": json.dumps(profile["sample_values"]),
            },
        )


def profile_dataset(dataset_name, persist=True):
    with engine.begin() as conn:
        profiles = collect_sql_profiles(conn, dataset_name)
        if persist:
            persist_profiles(conn, dataset_name, profiles)
    return profiles


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset_name")
    args = parser.parse_args()
    profiles = profile_dataset(args.dataset_name)
    for profile in profiles:
        print(f"Profiled {profile['column_name']}: {profile['inferred_type']}")
    print(f"Profiling complete: {len(profiles)} columns.")


if __name__ == "__main__":
    main()
