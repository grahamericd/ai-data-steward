import argparse
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import engine
from scripts.load_dataset import quote_identifier, validate_identifier


def load_reference_dataset(name, file_path, replace=False, source_version=None):
    with engine.begin() as conn:
        registration = conn.execute(
            text("""
                SELECT * FROM metadata.reference_dataset
                WHERE reference_dataset_name = :name AND active = TRUE
            """),
            {"name": name},
        ).mappings().first()
        if registration is None:
            raise ValueError(f"Reference dataset is not registered or active: {name}")

        schema = validate_identifier(registration["schema_name"], "reference schema")
        table_name = validate_identifier(registration["table_name"], "reference table")
        target_columns = set(
            conn.execute(
                text("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = :schema_name AND table_name = :table_name
                """),
                {"schema_name": schema, "table_name": table_name},
            ).scalars().all()
        )
        if replace:
            conn.execute(
                text(f"TRUNCATE TABLE {quote_identifier(schema)}.{quote_identifier(table_name)}")
            )

        rows_loaded = 0
        for chunk in pd.read_csv(file_path, dtype=str, chunksize=10_000):
            unexpected = set(chunk.columns) - target_columns
            if unexpected:
                raise ValueError(f"CSV contains unregistered columns: {sorted(unexpected)}")
            chunk.to_sql(
                table_name,
                conn,
                schema=schema,
                if_exists="append",
                index=False,
                method="multi",
                chunksize=1_000,
            )
            rows_loaded += len(chunk)

        conn.execute(
            text("""
                UPDATE metadata.reference_dataset
                SET refreshed_at = CURRENT_TIMESTAMP,
                    source_version = COALESCE(:source_version, source_version)
                WHERE reference_dataset_name = :name
            """),
            {"name": name, "source_version": source_version},
        )
    return rows_loaded


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("reference_dataset")
    parser.add_argument("file")
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--source-version")
    args = parser.parse_args()
    rows = load_reference_dataset(
        args.reference_dataset,
        args.file,
        replace=args.replace,
        source_version=args.source_version,
    )
    print(f"Reference rows loaded: {rows}")


if __name__ == "__main__":
    main()
