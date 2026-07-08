import os
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
import sys

#load_dotenv(os.path.expanduser("~/.datalab.env"))

#engine = create_engine(
#    f"postgresql+psycopg2://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}/{os.getenv('DB_NAME')}"
#)

engine = create_engine(
    "postgresql://egraham@localhost/florida_data_lab",
    connect_args={
        "password": "P@ssw0rd12345"
    }
)


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

    file_path = os.path.expanduser(
        f"~/projects/data-lab/raw_data/{dataset['source_file']}"
    )

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
    file_path = os.path.expanduser(
        f"~/projects/data-lab/raw_data/{dataset['source_file']}"
    )

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