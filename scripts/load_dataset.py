import argparse
import json
import os
import re
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import inspect, text


# ---------------------------------------------------------------------
# Project configuration
# ---------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import RAW_DATA_DIR, engine
from stewardship_context import get_stewardship_actor, get_stewardship_run_id


# ---------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------

STAGING_SCHEMA = "staging"

VALID_IDENTIFIER = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*$"
)


# ---------------------------------------------------------------------
# SQL identifier helpers
# ---------------------------------------------------------------------

def validate_identifier(value, description):
    """
    Ensure schema, table, and column names are safe to use in SQL.

    SQL parameters cannot be used for table or column names, so metadata
    identifiers must be validated before being included in SQL strings.
    """

    if not value:
        raise ValueError(
            f"Missing {description}."
        )

    if not VALID_IDENTIFIER.fullmatch(str(value)):
        raise ValueError(
            f"Invalid {description}: {value!r}. "
            "Only letters, numbers, and underscores are supported."
        )

    return str(value)


def quote_identifier(value):
    """Return a validated PostgreSQL identifier with double quotes."""

    validate_identifier(
        value,
        "SQL identifier",
    )

    return f'"{value}"'


# ---------------------------------------------------------------------
# Metadata queries
# ---------------------------------------------------------------------

def get_dataset_config(conn, dataset_name):
    """Retrieve an active dataset registration."""

    return conn.execute(
        text("""
            SELECT *
            FROM metadata.dataset
            WHERE dataset_name = :dataset_name
              AND active = TRUE
        """),
        {
            "dataset_name": dataset_name,
        },
    ).mappings().first()


def get_parser_definition(conn, dataset_id):
    """Retrieve the fixed-width parser definition for a dataset."""

    return conn.execute(
        text("""
            SELECT
                column_name,
                start_position,
                field_length,
                sequence_number
            FROM metadata.parser_definition
            WHERE dataset_id = :dataset_id
            ORDER BY sequence_number
        """),
        {
            "dataset_id": dataset_id,
        },
    ).mappings().all()


# ---------------------------------------------------------------------
# File handling
# ---------------------------------------------------------------------

def resolve_source_file(dataset, supplied_file=None):
    """
    Resolve the source file.

    If --file is supplied, use that file. Otherwise, preserve the original
    behavior and use metadata.dataset.source_file inside RAW_DATA_DIR.
    """

    if supplied_file:
        file_path = Path(
            supplied_file
        ).expanduser().resolve()

    else:
        registered_file = dataset.get(
            "source_file"
        )

        if not registered_file:
            raise ValueError(
                f"Dataset '{dataset['dataset_name']}' does not have "
                "a registered source_file, and no --file argument "
                "was supplied."
            )

        file_path = (
            RAW_DATA_DIR
            / registered_file
        ).resolve()

    if not file_path.exists():
        raise FileNotFoundError(
            f"Could not find source file: {file_path}"
        )

    if not file_path.is_file():
        raise ValueError(
            f"Source path is not a file: {file_path}"
        )

    return file_path

def add_file_lineage(
    dataframe,
    source_file,
    load_run_id,
):
    """
    Add latest-file lineage metadata to every incoming record.

    During upsert, these values replace the prior lineage values for
    records updated by the incoming file.
    """

    lineage_dataframe = dataframe.copy()

    lineage_dataframe["_source_file"] = source_file
    lineage_dataframe["_ingested_at"] = pd.Timestamp.now(tz="UTC")
    lineage_dataframe["_load_run_id"] = int(load_run_id)

    return lineage_dataframe
# ---------------------------------------------------------------------
# Fixed-width parsing
# ---------------------------------------------------------------------

def parse_fixed_width_record(line, fields):
    """Parse one fixed-width record using registered field positions."""

    row = {}

    for field in fields:
        name = field["column_name"]
        start = int(
            field["start_position"]
        )
        length = int(
            field["field_length"]
        )

        zero_based = start - 1

        row[name] = (
            line[
                zero_based:
                zero_based + length
            ]
            .replace("\x00", "")
            .strip()
        )

    return row


def parse_fixed_width(dataset, file_path):
    """Parse a registered fixed-width source file into a DataFrame."""

    with engine.begin() as conn:
        fields = get_parser_definition(
            conn,
            dataset["dataset_id"],
        )

    if not fields:
        raise ValueError(
            "No parser definition found for fixed-width dataset: "
            f"{dataset['dataset_name']}"
        )

    rows = []

    with open(
        file_path,
        "r",
        encoding="latin-1",
        errors="replace",
    ) as source:
        for line_number, line in enumerate(
            source,
            start=1,
        ):
            line = line.rstrip(
                "\r\n"
            )

            if not line.strip():
                continue

            try:
                row = parse_fixed_width_record(
                    line,
                    fields,
                )

                rows.append(
                    row
                )

            except Exception as exc:
                raise ValueError(
                    f"Could not parse line {line_number} "
                    f"of {file_path.name}: {exc}"
                ) from exc

    dataframe = pd.DataFrame(
        rows
    )

    if dataframe.empty:
        raise ValueError(
            f"No records were parsed from {file_path}."
        )

    return dataframe


# ---------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------

def parse_csv(file_path):
    """Parse a CSV source file into a DataFrame."""

    dataframe = pd.read_csv(
        file_path,
        dtype=str,
        keep_default_na=False,
        low_memory=False,
    )

    if dataframe.empty:
        raise ValueError(
            f"No records were parsed from {file_path}."
        )

    return dataframe


# ---------------------------------------------------------------------
# Primary-key validation
# ---------------------------------------------------------------------

def get_primary_key(dataset):
    """Return the registered single-column primary key."""

    primary_key = dataset.get(
        "primary_key"
    )

    if primary_key is None:
        return None

    primary_key = str(
        primary_key
    ).strip()

    if not primary_key:
        return None

    validate_identifier(
        primary_key,
        "primary-key column",
    )

    return primary_key


def validate_primary_key(dataframe, primary_key):
    """Validate primary-key values in the incoming incremental file."""

    if primary_key not in dataframe.columns:
        raise ValueError(
            f"Primary key '{primary_key}' was not found in the "
            f"incoming file. Available columns are: "
            f"{list(dataframe.columns)}"
        )

    key_values = dataframe[
        primary_key
    ]

    missing_mask = (
        key_values.isna()
        | key_values
        .fillna("")
        .astype(str)
        .str.strip()
        .eq("")
    )

    missing_count = int(
        missing_mask.sum()
    )

    if missing_count > 0:
        raise ValueError(
            f"{missing_count} incoming rows have a null or blank "
            f"value in primary key '{primary_key}'."
        )

    duplicate_mask = dataframe.duplicated(
        subset=[primary_key],
        keep=False,
    )

    duplicate_count = int(
        duplicate_mask.sum()
    )

    if duplicate_count > 0:
        duplicate_values = (
            dataframe.loc[
                duplicate_mask,
                primary_key,
            ]
            .astype(str)
            .drop_duplicates()
            .head(10)
            .tolist()
        )

        raise ValueError(
            f"The incoming file contains {duplicate_count} rows "
            f"with duplicate primary-key values in '{primary_key}'. "
            f"Sample duplicate values: {duplicate_values}"
        )


# ---------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------

def table_exists(schema_name, table_name):
    """Return True when the target table already exists."""

    inspector = inspect(
        engine
    )

    return inspector.has_table(
        table_name,
        schema=schema_name,
    )


def get_existing_columns(conn, schema_name, table_name):
    """Return the existing columns in a database table."""

    return conn.execute(
        text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = :schema_name
              AND table_name = :table_name
            ORDER BY ordinal_position
        """),
        {
            "schema_name": schema_name,
            "table_name": table_name,
        },
    ).scalars().all()


def validate_columns_match(
    dataframe,
    raw_schema,
    raw_table,
):
    """
    Ensure the incremental file matches the existing raw table.

    This prevents a source file with unexpected columns from silently
    changing or corrupting the target structure.
    """

    with engine.begin() as conn:
        existing_columns = set(get_existing_columns(
        conn,
        raw_schema,
        raw_table
        ))

    system_columns = {
    "_source_file",
    "_ingested_at",
    "_load_run_id"
    }


    incoming_columns = (
        set(dataframe.columns) - system_columns
        )

    required_columns = existing_columns - system_columns

    missing_columns = sorted(
        required_columns
        - incoming_columns
    )
  
    unexpected_columns = sorted(
        incoming_columns
        - required_columns
    )

    if missing_columns:
        raise ValueError(
            "The incremental file is missing columns expected by "
            f"{raw_schema}.{raw_table}: {missing_columns}"
        )

    if unexpected_columns:
        raise ValueError(
            "The incremental file contains columns that do not exist "
            f"in {raw_schema}.{raw_table}: {unexpected_columns}"
        )


def ensure_staging_schema():
    """Create the staging schema when it does not exist."""

    with engine.begin() as conn:
        conn.execute(
            text(f"""
                CREATE SCHEMA IF NOT EXISTS
                {quote_identifier(STAGING_SCHEMA)}
            """)
        )


def ensure_unique_index(
    raw_schema,
    raw_table,
    primary_key,
):
    """
    Ensure PostgreSQL has a unique index for ON CONFLICT.

    Existing duplicate primary keys will cause this operation to fail.
    """

    index_name = (
        f"ux_{raw_table}_{primary_key}"
    )

    # PostgreSQL identifier limit is 63 bytes.
    index_name = index_name[:60]

    with engine.begin() as conn:
        conn.execute(
            text(f"""
                CREATE UNIQUE INDEX IF NOT EXISTS
                {quote_identifier(index_name)}
                ON
                {quote_identifier(raw_schema)}.
                {quote_identifier(raw_table)}
                ({quote_identifier(primary_key)})
            """)
        )


# ---------------------------------------------------------------------
# Load modes
# ---------------------------------------------------------------------

def create_initial_table(
    dataframe,
    raw_schema,
    raw_table,
    primary_key,
):
    """Create the raw table when this is its first load."""

    dataframe.to_sql(
        raw_table,
        engine,
        schema=raw_schema,
        if_exists="fail",
        index=False,
        method="multi",
        chunksize=1000,
    )

    if primary_key:
        ensure_unique_index(
            raw_schema,
            raw_table,
            primary_key,
        )

    return {
        "rows_inserted": len(
            dataframe
        ),
        "rows_updated": 0,
    }


def replace_table(
    dataframe,
    raw_schema,
    raw_table,
    primary_key,
):
    """Replace the entire raw table."""

    dataframe.to_sql(
        raw_table,
        engine,
        schema=raw_schema,
        if_exists="replace",
        index=False,
        method="multi",
        chunksize=1000,
    )

    if primary_key:
        ensure_unique_index(
            raw_schema,
            raw_table,
            primary_key,
        )

    return {
        "rows_inserted": len(
            dataframe
        ),
        "rows_updated": 0,
    }


def append_table(
    dataframe,
    raw_schema,
    raw_table,
):
    """Append all incoming records."""

    dataframe.to_sql(
        raw_table,
        engine,
        schema=raw_schema,
        if_exists="append",
        index=False,
        method="multi",
        chunksize=1000,
    )

    return {
        "rows_inserted": len(
            dataframe
        ),
        "rows_updated": 0,
    }


def upsert_table(
    dataframe,
    raw_schema,
    raw_table,
    primary_key,
):
    """
    Insert new records and update records with existing primary keys.
    """

    ensure_staging_schema()

    ensure_unique_index(
        raw_schema,
        raw_table,
        primary_key,
    )

    staging_table = (
        f"{raw_table}_incremental"
    )

    validate_identifier(
        staging_table,
        "staging-table name",
    )

    dataframe.to_sql(
        staging_table,
        engine,
        schema=STAGING_SCHEMA,
        if_exists="replace",
        index=False,
        method="multi",
        chunksize=1000,
    )

    quoted_columns = ", ".join(
        quote_identifier(column)
        for column in dataframe.columns
    )

    update_columns = [
        column
        for column in dataframe.columns
        if column != primary_key
    ]

    if update_columns:
        update_clause = ", ".join(
            (
                f"{quote_identifier(column)} = "
                f"EXCLUDED.{quote_identifier(column)}"
            )
            for column in update_columns
        )

        conflict_action = (
            f"DO UPDATE SET {update_clause}"
        )

    else:
        conflict_action = (
            "DO NOTHING"
        )

    try:
        with engine.begin() as conn:
            rows_inserted = int(
                conn.execute(
                    text(f"""
                        SELECT COUNT(*)
                        FROM
                            {quote_identifier(STAGING_SCHEMA)}.
                            {quote_identifier(staging_table)}
                            AS incoming
                        LEFT JOIN
                            {quote_identifier(raw_schema)}.
                            {quote_identifier(raw_table)}
                            AS existing
                          ON
                            existing.{quote_identifier(primary_key)}
                            =
                            incoming.{quote_identifier(primary_key)}
                        WHERE
                            existing.{quote_identifier(primary_key)}
                            IS NULL
                    """)
                ).scalar_one()
            )

            rows_updated = int(
                conn.execute(
                    text(f"""
                        SELECT COUNT(*)
                        FROM
                            {quote_identifier(STAGING_SCHEMA)}.
                            {quote_identifier(staging_table)}
                            AS incoming
                        INNER JOIN
                            {quote_identifier(raw_schema)}.
                            {quote_identifier(raw_table)}
                            AS existing
                          ON
                            existing.{quote_identifier(primary_key)}
                            =
                            incoming.{quote_identifier(primary_key)}
                    """)
                ).scalar_one()
            )

            conn.execute(
                text(f"""
                    INSERT INTO
                        {quote_identifier(raw_schema)}.
                        {quote_identifier(raw_table)}
                        ({quoted_columns})
                    SELECT
                        {quoted_columns}
                    FROM
                        {quote_identifier(STAGING_SCHEMA)}.
                        {quote_identifier(staging_table)}
                    ON CONFLICT
                        ({quote_identifier(primary_key)})
                    {conflict_action}
                """)
            )

    finally:
        with engine.begin() as conn:
            conn.execute(
                text(f"""
                    DROP TABLE IF EXISTS
                    {quote_identifier(STAGING_SCHEMA)}.
                    {quote_identifier(staging_table)}
                """)
            )

    return {
        "rows_inserted": rows_inserted,
        "rows_updated": rows_updated,
    }


# Add these functions to scripts/load_dataset.py, above load_dataset().

def create_load_run(dataset, source_file, load_mode, initiated_by=None):
    """Create a running load-history record and return its ID."""

    with engine.begin() as conn:
        return int(
            conn.execute(
                text("""
                    INSERT INTO metadata.load_run
                    (
                        dataset_id,
                        dataset_name,
                        source_file,
                        source_type,
                        load_mode,
                        target_schema,
                        target_table,
                        primary_key_name,
                        status,
                        initiated_by
                        ,stewardship_run_id
                    )
                    VALUES
                    (
                        :dataset_id,
                        :dataset_name,
                        :source_file,
                        :source_type,
                        :load_mode,
                        :target_schema,
                        :target_table,
                        :primary_key_name,
                        'running',
                        :initiated_by
                        ,:stewardship_run_id
                    )
                    RETURNING load_run_id
                """),
                {
                    "dataset_id": dataset.get("dataset_id"),
                    "dataset_name": dataset.get("dataset_name"),
                    "source_file": source_file,
                    "source_type": dataset.get("source_type"),
                    "load_mode": load_mode,
                    "target_schema": dataset.get("raw_schema"),
                    "target_table": dataset.get("raw_table"),
                    "primary_key_name": dataset.get("primary_key"),
                    "initiated_by": initiated_by or get_stewardship_actor(),
                    "stewardship_run_id": get_stewardship_run_id(),
                },
            ).scalar_one()
        )


def complete_load_run(load_run_id, summary):
    """Mark a load-history record as completed."""

    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE metadata.load_run
                SET
                    status = 'completed',
                    completed_at = NOW(),
                    duration_seconds =
                        EXTRACT(EPOCH FROM (NOW() - started_at)),
                    rows_received = :rows_received,
                    rows_inserted = :rows_inserted,
                    rows_updated = :rows_updated,
                    details = CAST(:details AS JSONB)
                WHERE load_run_id = :load_run_id
            """),
            {
                "load_run_id": load_run_id,
                "rows_received": summary.get("rows_received"),
                "rows_inserted": summary.get("rows_inserted"),
                "rows_updated": summary.get("rows_updated"),
                "details": json.dumps(summary),
            },
        )


def fail_load_run(load_run_id, error_message):
    """Mark a load-history record as failed."""

    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE metadata.load_run
                SET
                    status = 'failed',
                    completed_at = NOW(),
                    duration_seconds =
                        EXTRACT(EPOCH FROM (NOW() - started_at)),
                    error_message = :error_message
                WHERE load_run_id = :load_run_id
            """),
            {
                "load_run_id": load_run_id,
                "error_message": str(error_message)[:10000],
            },
        )

# ---------------------------------------------------------------------
# Main dataset-loading workflow
# ---------------------------------------------------------------------

def load_dataset(
    dataset_name,
    supplied_file=None,
    requested_mode=None,
    source_file_label=None,
    initiated_by=None,
):
    """Load one registered dataset and record its load history."""

    load_run_id = None

    try:
        with engine.begin() as conn:
            dataset = get_dataset_config(
                conn,
                dataset_name,
            )

        if dataset is None:
            raise ValueError(
                f"Dataset not found or inactive: {dataset_name}"
            )

        dataset = dict(dataset)

        raw_schema = validate_identifier(
            dataset["raw_schema"],
            "raw schema",
        )

        raw_table = validate_identifier(
            dataset["raw_table"],
            "raw table",
        )

        source_type = dataset["source_type"]

        file_path = resolve_source_file(
            dataset,
            supplied_file,
        )

        registered_mode = (
            dataset.get("load_mode")
            or "upsert"
        )

        load_mode = (
            requested_mode
            or registered_mode
        ).lower()

        supported_modes = {
            "upsert",
            "append",
            "replace",
        }

        if load_mode not in supported_modes:
            raise ValueError(
                f"Unsupported load mode: {load_mode}. "
                f"Supported modes are: {sorted(supported_modes)}"
            )

        load_run_id = create_load_run(
            dataset=dataset,
            source_file=(
                source_file_label
                or file_path.name
            ),
            load_mode=load_mode,
            initiated_by=initiated_by,
        )
        
        


        if source_type == "fixed_width":
            dataframe = parse_fixed_width(
                dataset,
                file_path,
            )

        elif source_type == "csv":
            dataframe = parse_csv(file_path)

        else:
            raise ValueError(
                f"Unsupported source_type: {source_type}"
            )

        lineage_source_file = (
            source_file_label
            or file_path.name
        )

        dataframe = add_file_lineage(
            dataframe=dataframe,
            source_file=lineage_source_file,
            load_run_id=load_run_id,
        )


        primary_key = get_primary_key(dataset)

        if load_mode == "upsert":
            if not primary_key:
                raise ValueError(
                    f"Dataset '{dataset_name}' must have a primary key "
                    "before incremental upserts can be performed."
                )

            validate_primary_key(
                dataframe,
                primary_key,
            )

        target_exists = table_exists(
            raw_schema,
            raw_table,
        )

        print(f"Load run ID: {load_run_id}")
        print(f"Dataset: {dataset_name}")
        print(f"Source type: {source_type}")
        print(f"Source file: {file_path}")
        print(f"Load mode: {load_mode}")
        print(f"Rows parsed: {len(dataframe)}")
        print(f"Columns parsed: {len(dataframe.columns)}")
        print(f"Target table: {raw_schema}.{raw_table}")

        if not target_exists:
            print(
                "Target table does not exist. Creating initial table."
            )
            result = create_initial_table(
                dataframe,
                raw_schema,
                raw_table,
                primary_key,
            )

        elif load_mode == "replace":
            result = replace_table(
                dataframe,
                raw_schema,
                raw_table,
                primary_key,
            )

        elif load_mode == "append":
            validate_columns_match(
                dataframe,
                raw_schema,
                raw_table,
            )
            result = append_table(
                dataframe,
                raw_schema,
                raw_table,
            )

        elif load_mode == "upsert":
            validate_columns_match(
                dataframe,
                raw_schema,
                raw_table,
            )
            result = upsert_table(
                dataframe,
                raw_schema,
                raw_table,
                primary_key,
            )

        else:
            raise ValueError(
                f"Unhandled load mode: {load_mode}"
            )

        summary = {
            "status": "completed",
            "load_run_id": load_run_id,
            "dataset_name": dataset_name,
            "source_file": (
                source_file_label
                or file_path.name
            ),
            "source_type": source_type,
            "load_mode": load_mode,
            "primary_key": primary_key,
            "target_table": f"{raw_schema}.{raw_table}",
            "rows_received": len(dataframe),
            "rows_inserted": result["rows_inserted"],
            "rows_updated": result["rows_updated"],
        }

        complete_load_run(
            load_run_id,
            summary,
        )

        print(f"Rows inserted: {summary['rows_inserted']}")
        print(f"Rows updated: {summary['rows_updated']}")
        print(f"Loaded {raw_schema}.{raw_table}")
        print(json.dumps(summary))

        return summary

    except Exception as exc:
        if load_run_id is not None:
            try:
                fail_load_run(
                    load_run_id,
                    exc,
                )
            except Exception as history_exc:
                print(
                    f"Could not update load history: {history_exc}",
                    file=sys.stderr,
                )

        raise


# ---------------------------------------------------------------------
# Command-line interface
# ---------------------------------------------------------------------

def parse_arguments():
    parser = argparse.ArgumentParser(
        description=(
            "Load a registered dataset into its raw PostgreSQL table."
        )
    )

    parser.add_argument(
        "dataset_name",
        help=(
            "Dataset name from metadata.dataset."
        ),
    )

    parser.add_argument(
        "--file",
        dest="source_file",
        help=(
            "Optional incoming file path. When omitted, the loader "
            "uses metadata.dataset.source_file in RAW_DATA_DIR."
        ),
    )

    parser.add_argument(
        "--mode",
        choices=[
            "upsert",
            "append",
            "replace",
        ],
        default=None,
        help=(
            "Optional load-mode override. Incremental files should "
            "normally use upsert."
        ),
    )

    return parser.parse_args()


def main():
    args = parse_arguments()

    try:
        load_dataset(
            dataset_name=args.dataset_name,
            supplied_file=args.source_file,
            requested_mode=args.mode,
        )

    except Exception as exc:
        failure = {
            "status": "failed",
            "dataset_name": args.dataset_name,
            "source_file": args.source_file,
            "error": str(exc),
        }

        print(
            f"Load failed: {exc}",
            file=sys.stderr,
        )

        # Streamlit can also parse this JSON failure response.
        print(
            json.dumps(
                failure
            )
        )

        sys.exit(1)


if __name__ == "__main__":
    main()
