import json
import sys
from pathlib import Path

from sqlalchemy import text


# ---------------------------------------------------------------------
# Project imports
# ---------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import engine

from rule_registry import (
    validate_executable_rule,
)

# ---------------------------------------------------------------------
# Command-line argument
# ---------------------------------------------------------------------

if len(sys.argv) != 2:
    print(
        "Usage: python generate_dataset_rules.py <dataset_name>"
    )
    sys.exit(1)

DATASET_NAME = sys.argv[1]


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def get_dataset(conn, dataset_name):
    """
    Retrieve the active dataset registration.
    """

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


def rule_exists(
    conn,
    dataset_name,
    executable_rule,
):
    """
    Return True if an equivalent active DATASET rule already exists.
    """

    existing_rules = conn.execute(
        text("""
            SELECT
                id,
                rule_definition
            FROM dq.rule
            WHERE dataset_name = :dataset_name
              AND rule_scope = 'DATASET'
              AND status NOT IN (
                  'rejected',
                  'retired'
              )
        """),
        {
            "dataset_name": dataset_name,
        },
    ).mappings().all()

    for existing_rule in existing_rules:

        definition = existing_rule[
            "rule_definition"
        ]

        if isinstance(
            definition,
            str,
        ):
            try:
                definition = json.loads(
                    definition
                )

            except json.JSONDecodeError:
                continue

        if not isinstance(
            definition,
            dict,
        ):
            continue

        existing_executable = definition.get(
            "executable_rule"
        )

        if existing_executable == executable_rule:
            return True

    return False


def insert_rule(
    conn,
    dataset_name,
    target_columns,
    rule_definition,
):
    """
    Insert one proposed DATASET-scope rule.
    """

    conn.execute(
        text("""
            INSERT INTO dq.rule
            (
                dataset_name,
                column_name,
                rule_type,
                rule_definition,
                status,
                rule_scope,
                target_columns,
                llm_provider,
                llm_model,
                prompt_version
            )
            VALUES
            (
                :dataset_name,
                NULL,
                'system_generated',
                CAST(:rule_definition AS jsonb),
                'proposed',
                'DATASET',
                CAST(:target_columns AS jsonb),
                NULL,
                NULL,
                'dataset-rule-v1'
            )
        """),
        {
            "dataset_name": dataset_name,

            "rule_definition": json.dumps(
                rule_definition
            ),

            "target_columns": json.dumps(
                target_columns
            ),
        },
    )


# ---------------------------------------------------------------------
# Rule generation
# ---------------------------------------------------------------------

def build_dataset_rule_proposals(dataset):
    """
    Build deterministic DATASET-level rules from trusted metadata.

    No LLM is used here.
    """

    proposals = []

    # -------------------------------------------------------------
    # Rule 1:
    # Active datasets should contain at least one record.
    # -------------------------------------------------------------

    proposals.append(
        {
            "target_columns": [],

            "rule_definition": {
                "business_definition": (
                    "The dataset must contain at least one record."
                ),

                "confidence_score": 1.0,

                "evidence": (
                    "The dataset is registered as active and should "
                    "contain records when evaluated."
                ),

                "target_columns": [],

                "executable_rule": {
                    "type": "minimum_row_count",

                    "parameters": {
                        "minimum_rows": 1
                    }
                }
            }
        }
    )

    # -------------------------------------------------------------
    # Rule 2:
    # Registered primary keys must be unique.
    # -------------------------------------------------------------

    primary_key = dataset.get(
        "primary_key"
    )

    if primary_key:

        proposals.append(
            {
                "target_columns": [
                    primary_key
                ],

                "rule_definition": {
                    "business_definition": (
                        f"{primary_key} must uniquely identify "
                        "each record in the dataset."
                    ),

                    "confidence_score": 1.0,

                    "evidence": (
                        f"{primary_key} is registered as the "
                        "dataset primary key."
                    ),

                    "target_columns": [
                        primary_key
                    ],

                    "executable_rule": {
                        "type": "primary_key_unique",

                        "parameters": {
                            "column": primary_key
                        }
                    }
                }
            }
        )

    return proposals


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main():

    print(
        f"Generating DATASET rules for: {DATASET_NAME}"
    )

    with engine.begin() as conn:

        dataset = get_dataset(
            conn,
            DATASET_NAME,
        )

        if dataset is None:
            raise ValueError(
                f"Dataset not found or inactive: "
                f"{DATASET_NAME}"
            )

        dataset = dict(
            dataset
        )

        proposals = build_dataset_rule_proposals(
            dataset
        )

        inserted = 0
        duplicates = 0

        for proposal in proposals:

            rule_definition = proposal[
                "rule_definition"
            ]

            executable_rule = rule_definition[
                "executable_rule"
            ]

            if rule_exists(
                conn,
                DATASET_NAME,
                executable_rule,
            ):
                duplicates += 1

                print(
                    "Skipped duplicate: "
                    f"{executable_rule['type']}"
                )

                continue

            insert_rule(
                conn,
                DATASET_NAME,
                proposal[
                    "target_columns"
                ],
                rule_definition,
            )

            inserted += 1

            print(
                "Inserted proposed DATASET rule: "
                f"{executable_rule['type']}"
            )

    print()
    print(
        "Dataset rule generation complete."
    )

    print(
        f"Inserted: {inserted}"
    )

    print(
        f"Duplicates skipped: {duplicates}"
    )


if __name__ == "__main__":
    main()