import os
import sys
from pathlib import Path
import pandas as pd
from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
    
from config import RAW_DATA_DIR, engine


def get_dataset_config(conn, dataset_name):
    return conn.execute(
        text("""
            SELECT *
            FROM metadata.dataset
            WHERE dataset_name = :dataset_name
              AND active = TRUE
        """),
        {"dataset_name": dataset_name},
    ).mappings().first()


def get_parser_definition(conn, dataset_id):
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
        {"dataset_id": dataset_id},
    ).mappings().all()


def parse_fixed_width_record(line, fields):
    row = {}
    for field in fields:
        name = field["column_name"]
        start = field["start_position"]
        length = field["field_length"]
        zero_based = start - 1

        row[name] = (
            line[zero_based:zero_based + length]
            .replace("\x00", "")
            .strip()
        )
    return row

def load_fixed_width(dataset):
    with engine.begin() as conn:
        fields = get_parser_definition(conn, dataset["dataset_id"])
    if not fields:
        raise ValueError(
            f"No parser definition found for fixed-width dataset: {dataset['dataset_name']}"
        )
    file_path = RAW_DATA_DIR / dataset["source_file"]
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Could not find source file: {file_path}")
    rows = []
    with open(file_path, "r", encoding="latin-1", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            rows.append(parse_fixed_width_record(line, fields))

    df = pd.DataFrame(rows)
    print(f"Dataset: {dataset['dataset_name']}")
    print(f"Source type: fixed_width")
    print(f"Source file: {file_path}")
    print(f"Rows parsed: {len(df)}")
    print(f"Columns parsed: {len(df.columns)}")
    df.to_sql(
        dataset["raw_table"],
        engine,
        schema=dataset["raw_schema"],
        if_exists="replace",
        index=False,
    )
    print(f"Loaded {dataset['raw_schema']}.{dataset['raw_table']}")


def load_csv(dataset):
    file_path = RAW_DATA_DIR / dataset["source_file"]
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Could not find source file: {file_path}")
    df = pd.read_csv(file_path)
    print(f"Dataset: {dataset['dataset_name']}")
    print(f"Source type: csv")
    print(f"Source file: {file_path}")
    print(f"Rows parsed: {len(df)}")
    print(f"Columns parsed: {len(df.columns)}")
    df.to_sql(
        dataset["raw_table"],
        engine,
        schema=dataset["raw_schema"],
        if_exists="replace",
        index=False,
    )
    print(f"Loaded {dataset['raw_schema']}.{dataset['raw_table']}")


def main():
    if len(sys.argv) != 2:
        print("Usage: python load_dataset.py <dataset_name>")
        sys.exit(1)
    dataset_name = sys.argv[1]
    with engine.begin() as conn:
        dataset = get_dataset_config(conn, dataset_name)
    if dataset is None:
        raise ValueError(f"Dataset not found or inactive: {dataset_name}")
    source_type = dataset["source_type"]
    if source_type == "fixed_width":
        load_fixed_width(dataset)
    elif source_type == "csv":
        load_csv(dataset)
    else:
        raise ValueError(f"Unsupported source_type: {source_type}")

if __name__ == "__main__":
    main()