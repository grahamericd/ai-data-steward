import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import engine
from remediation_decision import change_remediation_status
from scripts.load_dataset import quote_identifier, validate_identifier
from stewardship_context import get_stewardship_actor, get_stewardship_run_id


def get_dataset(conn, dataset_name):
    return conn.execute(
        text("""
            SELECT * FROM metadata.dataset
            WHERE dataset_name = :dataset_name AND active = TRUE
        """),
        {"dataset_name": dataset_name},
    ).mappings().first()


def create_remediation_run(dataset, actor):
    with engine.begin() as conn:
        return int(
            conn.execute(
                text("""
                    INSERT INTO curated.remediation_run
                    (
                        dataset_id, dataset_name, stewardship_run_id,
                        initiated_by, status
                    )
                    VALUES
                    (
                        :dataset_id, :dataset_name, :stewardship_run_id,
                        :actor, 'running'
                    )
                    RETURNING remediation_run_id
                """),
                {
                    "dataset_id": dataset["dataset_id"],
                    "dataset_name": dataset["dataset_name"],
                    "stewardship_run_id": get_stewardship_run_id(),
                    "actor": actor,
                },
            ).scalar_one()
        )


def mark_remediation_run_failed(remediation_run_id, error):
    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE curated.remediation_run
                SET status = 'failed', completed_at = CURRENT_TIMESTAMP,
                    error_message = :error
                WHERE remediation_run_id = :run_id
            """),
            {"run_id": remediation_run_id, "error": str(error)[:10000]},
        )


def curated_physical_table_name(raw_table, version_id):
    """Return the immutable physical table name for a curated version."""
    base_name = validate_identifier(raw_table, "raw table")[:42]
    return f"{base_name}__v{int(version_id)}"


def create_version_record(conn, dataset, remediation_run_id, actor, previous_version_id):
    pending_name = f"pending_{remediation_run_id}"
    version_id = int(
        conn.execute(
            text("""
                INSERT INTO curated.dataset_version
                (
                    dataset_id, dataset_name, remediation_run_id,
                    stewardship_run_id, previous_version_id,
                    physical_table_name, created_by, status
                )
                VALUES
                (
                    :dataset_id, :dataset_name, :remediation_run_id,
                    :stewardship_run_id, :previous_version_id,
                    :physical_table_name, :actor, 'building'
                )
                RETURNING curated_version_id
            """),
            {
                "dataset_id": dataset["dataset_id"],
                "dataset_name": dataset["dataset_name"],
                "remediation_run_id": remediation_run_id,
                "stewardship_run_id": get_stewardship_run_id(),
                "previous_version_id": previous_version_id,
                "physical_table_name": pending_name,
                "actor": actor,
            },
        ).scalar_one()
    )
    physical_table = curated_physical_table_name(dataset["raw_table"], version_id)
    conn.execute(
        text("""
            UPDATE curated.dataset_version
            SET physical_table_name = :physical_table
            WHERE curated_version_id = :version_id
        """),
        {"physical_table": physical_table, "version_id": version_id},
    )
    return version_id, physical_table


def apply_version(conn, dataset, remediation_run_id, actor):
    raw_schema = validate_identifier(dataset["raw_schema"] or "raw", "raw schema")
    raw_table = validate_identifier(dataset["raw_table"], "raw table")
    primary_key = validate_identifier(dataset["primary_key"], "primary key")

    previous = conn.execute(
        text("""
            SELECT curated_version_id, physical_table_name
            FROM curated.dataset_version
            WHERE dataset_name = :dataset_name AND status = 'completed'
            ORDER BY curated_version_id DESC
            LIMIT 1
        """),
        {"dataset_name": dataset["dataset_name"]},
    ).mappings().first()
    previous_version_id = previous["curated_version_id"] if previous else None
    version_id, physical_table = create_version_record(
        conn, dataset, remediation_run_id, actor, previous_version_id
    )
    quoted_version = quote_identifier(physical_table)

    if previous:
        previous_table = quote_identifier(
            validate_identifier(previous["physical_table_name"], "previous curated table")
        )
        conn.execute(
            text(f'CREATE TABLE curated.{quoted_version} AS SELECT * FROM curated.{previous_table}')
        )
        conn.execute(
            text(f'UPDATE curated.{quoted_version} SET "_curated_version_id" = :version_id'),
            {"version_id": version_id},
        )
        conn.execute(
            text("""
                INSERT INTO curated.row_lineage
                (curated_version_id, source_row_identifier, raw_load_run_id, source_file)
                SELECT :version_id, source_row_identifier, raw_load_run_id, source_file
                FROM curated.row_lineage
                WHERE curated_version_id = :previous_version_id
            """),
            {"version_id": version_id, "previous_version_id": previous_version_id},
        )
    else:
        conn.execute(
            text(f"""
                CREATE TABLE curated.{quoted_version} AS
                SELECT raw_source.*,
                       :version_id::bigint AS "_curated_version_id",
                       raw_source.{quote_identifier(primary_key)}::text
                           AS "_raw_source_row_identifier"
                FROM {quote_identifier(raw_schema)}.{quote_identifier(raw_table)} raw_source
            """),
            {"version_id": version_id},
        )
        conn.execute(
            text(f"""
                INSERT INTO curated.row_lineage
                (curated_version_id, source_row_identifier, raw_load_run_id, source_file)
                SELECT :version_id,
                       {quote_identifier(primary_key)}::text,
                       "_load_run_id",
                       "_source_file"
                FROM {quote_identifier(raw_schema)}.{quote_identifier(raw_table)}
            """),
            {"version_id": version_id},
        )

    available_columns = set(
        conn.execute(
            text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'curated' AND table_name = :table_name
            """),
            {"table_name": physical_table},
        ).scalars().all()
    )
    remediations = conn.execute(
        text("""
            SELECT * FROM dq.remediation_suggestion
            WHERE dataset_name = :dataset_name
              AND status = 'approved'
              AND rule_id IS NOT NULL AND result_id IS NOT NULL
              AND failed_record_id IS NOT NULL
            ORDER BY id
        """),
        {"dataset_name": dataset["dataset_name"]},
    ).mappings().all()

    applied = 0
    change_count = 0
    for remediation in remediations:
        suggested_values = remediation["suggested_values"]
        if isinstance(suggested_values, str):
            suggested_values = json.loads(suggested_values)
        if not remediation["source_row_identifier"] or not isinstance(suggested_values, dict):
            continue

        remediation_changed = False
        for column_name, new_value in suggested_values.items():
            if column_name == primary_key:
                continue
            validate_identifier(column_name, "remediation column")
            if column_name not in available_columns:
                continue
            old_value = conn.execute(
                text(f"""
                    SELECT {quote_identifier(column_name)}::text
                    FROM curated.{quoted_version}
                    WHERE {quote_identifier(primary_key)} = :pk_value
                """),
                {"pk_value": remediation["source_row_identifier"]},
            ).scalar_one_or_none()
            if old_value == (None if new_value is None else str(new_value)):
                continue
            update = conn.execute(
                text(f"""
                    UPDATE curated.{quoted_version}
                    SET {quote_identifier(column_name)} = :new_value
                    WHERE {quote_identifier(primary_key)} = :pk_value
                """),
                {"new_value": new_value, "pk_value": remediation["source_row_identifier"]},
            )
            if update.rowcount <= 0:
                continue
            conn.execute(
                text("""
                    INSERT INTO curated.change_history
                    (
                        curated_version_id, previous_version_id, remediation_id,
                        rule_id, result_id, failed_record_id,
                        source_row_identifier, column_name, previous_value, new_value
                    )
                    VALUES
                    (
                        :version_id, :previous_version_id, :remediation_id,
                        :rule_id, :result_id, :failed_record_id,
                        :source_row_identifier, :column_name, :previous_value, :new_value
                    )
                """),
                {
                    "version_id": version_id,
                    "previous_version_id": previous_version_id,
                    "remediation_id": remediation["id"],
                    "rule_id": remediation["rule_id"],
                    "result_id": remediation["result_id"],
                    "failed_record_id": remediation["failed_record_id"],
                    "source_row_identifier": remediation["source_row_identifier"],
                    "column_name": column_name,
                    "previous_value": old_value,
                    "new_value": None if new_value is None else str(new_value),
                },
            )
            remediation_changed = True
            change_count += 1

        if remediation_changed:
            change_remediation_status(
                conn,
                remediation["id"],
                "applied",
                changed_by=actor,
                decision_note=f"Applied in curated remediation run {remediation_run_id}.",
            )
            conn.execute(
                text("""
                    UPDATE dq.remediation_suggestion
                    SET applied_in_remediation_run_id = :run_id
                    WHERE id = :id
                """),
                {"run_id": remediation_run_id, "id": remediation["id"]},
            )
            applied += 1

    row_count = int(
        conn.execute(text(f"SELECT COUNT(*) FROM curated.{quoted_version}")).scalar_one()
    )
    current_view = quote_identifier(f"{raw_table[:50]}__current")
    conn.execute(
        text(f"CREATE OR REPLACE VIEW curated.{current_view} AS SELECT * FROM curated.{quoted_version}")
    )
    conn.execute(
        text("""
            UPDATE curated.dataset_version
            SET status = 'completed', row_count = :row_count
            WHERE curated_version_id = :version_id
        """),
        {"version_id": version_id, "row_count": row_count},
    )
    conn.execute(
        text("""
            UPDATE curated.remediation_run
            SET status = 'completed', completed_at = CURRENT_TIMESTAMP,
                remediation_count = :applied
            WHERE remediation_run_id = :run_id
        """),
        {"run_id": remediation_run_id, "applied": applied},
    )
    return {
        "curated_version_id": version_id,
        "previous_version_id": previous_version_id,
        "physical_table_name": physical_table,
        "row_count": row_count,
        "remediations_applied": applied,
        "field_changes": change_count,
    }


def apply_remediations(dataset_name, actor):
    with engine.begin() as conn:
        dataset = get_dataset(conn, dataset_name)
    if not dataset:
        raise ValueError(f"Dataset not found or inactive: {dataset_name}")
    dataset = dict(dataset)
    if not dataset.get("primary_key"):
        raise ValueError(f"Dataset '{dataset_name}' must have a primary key.")

    remediation_run_id = create_remediation_run(dataset, actor)
    try:
        with engine.begin() as conn:
            summary = apply_version(conn, dataset, remediation_run_id, actor)
        return {"remediation_run_id": remediation_run_id, **summary}
    except Exception as exc:
        mark_remediation_run_failed(remediation_run_id, exc)
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset_name")
    parser.add_argument("--actor", default=get_stewardship_actor() or os.getenv("USER") or "cli-user")
    args = parser.parse_args()
    summary = apply_remediations(args.dataset_name, args.actor.strip() or "cli-user")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
