import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import URL
import os
import json
import re
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
    
from config import RAW_DATA_DIR, engine

    

def infer_type(series):
    sample = series.dropna().astype(str)
    if len(sample) == 0:
        return "unknown"
    if sample.str.match(r'^\$[\d,]+$').all():
        return "currency"
    if sample.str.match(r'^\d+(\.\d+)?%$').all():
        return "percentage"
    if sample.str.match(r'^\d+$').all():
        return "integer"
    if sample.str.match(r"^\d{4}$").all():
        return "year"
    return "text"

def get_dataset_config(conn, dataset_name):
    return conn.execute(
        text("""
            SELECT *
            FROM metadata.dataset
            WHERE dataset_name = :dataset_name
              AND active = TRUE
        """),
        {"dataset_name": dataset_name}
    ).mappings().first()

def clean_sample_values(series, limit=10):
    return (
        series.dropna()
        .astype(str)
        .str.strip()
        .drop_duplicates()
        .head(limit)
        .tolist()
    )


def profile_column(series):
    sample = series.dropna().astype(str).str.strip()

    return {
        "row_count": int(len(series)),
        "null_count": int(series.isna().sum()),
        "null_percent": float(series.isna().sum() / len(series) * 100),
        "distinct_count": int(series.nunique(dropna=True)),
        "sample_values": clean_sample_values(series),
        "inferred_type": infer_type(series),
        "min_value": sample.min() if len(sample) > 0 else None,
        "max_value": sample.max() if len(sample) > 0 else None,
    }


# -----------------------------
# Main profile process
# -----------------------------
if len(sys.argv) != 2:
    print("Usage: python profile_dataset.py <dataset_name>")
    sys.exit(1)

DATASET_NAME = sys.argv[1]

with engine.begin() as conn:
    dataset = get_dataset_config(conn, DATASET_NAME)

if dataset is None:
    raise ValueError(f"Dataset not found: {DATASET_NAME}")

source_schema = dataset["raw_schema"]
source_table = dataset["raw_table"]

#DATASET_NAME = "corporate_data"
#SOURCE_TABLE = "raw.corporate_data"

#df = pd.read_sql(f"SELECT * FROM {SOURCE_TABLE}", engine)

df = pd.read_sql(
    f'SELECT * FROM {source_schema}."{source_table}"',
    engine
)

with engine.begin() as conn:
    # Optional: clear old profiles for this dataset
    conn.execute(
        text("""
            DELETE FROM metadata.column_profile
            WHERE dataset_name = :dataset_name
        """),
        {"dataset_name": DATASET_NAME}
    )

    for column in df.columns:
        profile = profile_column(df[column])

        conn.execute(
            text("""
                INSERT INTO metadata.column_profile
                (
                    dataset_name,
                    column_name,
                    row_count,
                    null_count,
                    null_percent,
                    distinct_count,
                    sample_values,
                    inferred_type,
                    min_value,
                    max_value
                )
                VALUES
                (
                    :dataset_name,
                    :column_name,
                    :row_count,
                    :null_count,
                    :null_percent,
                    :distinct_count,
                    CAST(:sample_values AS jsonb),
                    :inferred_type,
                    :min_value,
                    :max_value
                )
            """),
            {
                "dataset_name": DATASET_NAME,
                "column_name": column,
                "row_count": profile["row_count"],
                "null_count": profile["null_count"],
                "null_percent": profile["null_percent"],
                "distinct_count": profile["distinct_count"],
                "sample_values": json.dumps(profile["sample_values"]),
                "inferred_type": profile["inferred_type"],
                "min_value": profile["min_value"],
                "max_value": profile["max_value"],
            }
        )

        print(f"Profiled {column}: {profile['inferred_type']}")

print("Profiling complete.")

