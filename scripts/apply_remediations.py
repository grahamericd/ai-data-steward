import os
import sys
import json
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

load_dotenv()

DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_NAME = os.getenv("DB_NAME")


engine = create_engine(
    URL.create(
        "postgresql+psycopg2",
        username=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        database=DB_NAME,
    )
 )

if len(sys.argv) != 2:
    print("Usage: python apply_remediations.py <dataset_name>")
    sys.exit(1)

dataset_name = sys.argv[1]

with engine.begin() as conn:
    dataset = conn.execute(
        text("""
            SELECT *
            FROM metadata.dataset
            WHERE dataset_name = :dataset_name
              AND active = TRUE
        """),
        {"dataset_name": dataset_name}
    ).mappings().first()

    if not dataset:
        raise ValueError(f"Dataset not found: {dataset_name}")

    raw_schema = dataset["raw_schema"]
    raw_table = dataset["raw_table"]
    primary_key = dataset["primary_key"]

    if not primary_key:
        raise ValueError("Dataset must have a primary_key to apply remediations.")

    curated_table = raw_table

    print(f"Creating curated.{curated_table} from {raw_schema}.{raw_table}")

    conn.execute(text(f'DROP TABLE IF EXISTS curated."{curated_table}"'))

    conn.execute(text(f"""
        CREATE TABLE curated."{curated_table}" AS
        SELECT *
        FROM {raw_schema}."{raw_table}"
    """))

    remediations = conn.execute(
        text("""
            SELECT *
            FROM dq.remediation_suggestion
            WHERE dataset_name = :dataset_name
              AND status = 'approved'
            ORDER BY id
        """),
        {"dataset_name": dataset_name}
    ).mappings().all()

    print(f"Approved remediations found: {len(remediations)}")

    applied = 0

    for remediation in remediations:
        pk_value = remediation["source_row_identifier"]
        suggested_values = remediation["suggested_values"]

        if isinstance(suggested_values, str):
            suggested_values = json.loads(suggested_values)

        updates = {
            k: v
            for k, v in suggested_values.items()
            if k not in [primary_key]
        }

        for column_name, new_value in updates.items():
            conn.execute(
                text(f"""
                    UPDATE curated."{curated_table}"
                    SET "{column_name}" = :new_value
                    WHERE "{primary_key}" = :pk_value
                """),
                {
                    "new_value": new_value,
                    "pk_value": pk_value
                }
            )

        conn.execute(
            text("""
                UPDATE dq.remediation_suggestion
                SET status = 'applied'
                WHERE id = :id
            """),
            {"id": remediation["id"]}
        )

        applied += 1

    print(f"Remediations applied: {applied}")
    print(f"Curated table ready: curated.{curated_table}")