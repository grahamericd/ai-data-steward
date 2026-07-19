import json
import sys
from pathlib import Path

from sqlalchemy import text


# Make the project root importable.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import engine


def get_dataset(conn, dataset_name):
    return conn.execute(
        text("""
            SELECT *
            FROM metadata.dataset
            WHERE dataset_name = :dataset_name
              AND active = TRUE
        """),
        {"dataset_name": dataset_name},
    ).mappings().first()


def main():
    if len(sys.argv) != 2:
        print("Usage: python apply_remediations.py <dataset_name>")
        sys.exit(1)

    dataset_name = sys.argv[1]

    with engine.begin() as conn:
        dataset = get_dataset(conn, dataset_name)

        if not dataset:
            raise ValueError(
                f"Dataset not found or inactive: {dataset_name}"
            )

        raw_schema = dataset["raw_schema"]
        raw_table = dataset["raw_table"]
        primary_key = dataset["primary_key"]

        if not primary_key:
            raise ValueError(
                f"Dataset '{dataset_name}' must have a primary key "
                "before remediations can be applied."
            )

        curated_table = raw_table

        print(
            f"Creating curated.{curated_table} "
            f"from {raw_schema}.{raw_table}"
        )

        conn.execute(
            text(f'DROP TABLE IF EXISTS curated."{curated_table}"')
        )

        conn.execute(
            text(f"""
                CREATE TABLE curated."{curated_table}" AS
                SELECT *
                FROM "{raw_schema}"."{raw_table}"
            """)
        )

        remediations = conn.execute(
            text("""
                SELECT *
                FROM dq.remediation_suggestion
                WHERE dataset_name = :dataset_name
                  AND status = 'approved'
                ORDER BY id
            """),
            {"dataset_name": dataset_name},
        ).mappings().all()

        print(f"Approved remediations found: {len(remediations)}")

        applied = 0
        skipped = 0

        for remediation in remediations:
            remediation_id = remediation["id"]
            pk_value = remediation["source_row_identifier"]
            suggested_values = remediation["suggested_values"]

            if not pk_value:
                print(
                    f"Skipping remediation {remediation_id}: "
                    "missing source row identifier."
                )
                skipped += 1
                continue

            if isinstance(suggested_values, str):
                suggested_values = json.loads(suggested_values)

            if not isinstance(suggested_values, dict):
                print(
                    f"Skipping remediation {remediation_id}: "
                    "suggested_values is not a JSON object."
                )
                skipped += 1
                continue

            updates = {
                column_name: new_value
                for column_name, new_value in suggested_values.items()
                if column_name != primary_key
            }

            if not updates:
                print(
                    f"Skipping remediation {remediation_id}: "
                    "no suggested field updates."
                )
                skipped += 1
                continue

            row_updated = False

            for column_name, new_value in updates.items():
                result = conn.execute(
                    text(f"""
                        UPDATE curated."{curated_table}"
                        SET "{column_name}" = :new_value
                        WHERE "{primary_key}" = :pk_value
                    """),
                    {
                        "new_value": new_value,
                        "pk_value": pk_value,
                    },
                )

                if result.rowcount > 0:
                    row_updated = True

            if not row_updated:
                print(
                    f"Skipping remediation {remediation_id}: "
                    f"no curated record matched "
                    f"{primary_key}={pk_value!r}."
                )
                skipped += 1
                continue

            conn.execute(
                text("""
                    UPDATE dq.remediation_suggestion
                    SET status = 'applied'
                    WHERE id = :id
                """),
                {"id": remediation_id},
            )

            applied += 1

        print(f"Remediations applied: {applied}")
        print(f"Remediations skipped: {skipped}")
        print(f"Curated table ready: curated.{curated_table}")


if __name__ == "__main__":
    main()