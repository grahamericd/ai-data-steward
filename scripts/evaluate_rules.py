import os
import json
import re
import pandas as pd
from pathlib import Path
from sqlalchemy import text
from sqlalchemy.engine import URL
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
    
from config import RAW_DATA_DIR, engine


if len(sys.argv) != 2:
    print("Usage: python evaluate_rules.py <dataset_name>")
    sys.exit(1)

DATASET_NAME = sys.argv[1]


def percent_to_number(value):
    if value is None:
        return None
    value = str(value).strip()
    if value == "":
        return None
    value = value.replace("%", "")
    try:
        return float(value)
    except ValueError:
        return None


def evaluate_percentage_range(df, column_name, min_value=0, max_value=100):
    values = df[column_name].apply(percent_to_number)
    failed_rows = df[
        values.isna() |
        (values < min_value) |
        (values > max_value)
    ]
    return {
        "rows_checked": len(df),
        "failed_count": len(failed_rows),
        "sample_failures": failed_rows.head(10).to_dict(orient="records")
    }

def evaluate_allowed_values(df, column_name, allowed_values):
    values = df[column_name].astype(str).str.strip()
    failed_rows = df[
        ~values.isin(allowed_values)
    ]
    return {
        "rows_checked": len(df),
        "failed_count": len(failed_rows),
        "allowed_values": allowed_values,
        "sample_failures": failed_rows.head(10).to_dict(orient="records")
    }

def evaluate_city_contains_state_or_zip(df, city_column):
    pattern = r"\b[A-Z]{2}\b|\b\d{5}(-\d{4})?\b"

    values = df[city_column].fillna("").astype(str).str.strip().str.upper()

    failed_rows = df[values.str.contains(pattern, regex=True, na=False)]

    return {
        "rows_checked": len(df),
        "failed_count": len(failed_rows),
        "sample_failures": failed_rows.head(10).to_dict(orient="records")
    }


def evaluate_state_field_contains_zip(df, state_column):
    values = df[state_column].fillna("").astype(str).str.strip().str.upper()

    failed_rows = df[
        values.str.match(r"^\d{5}(-\d{4})?$", na=False) |
        values.str.match(r"^[A-Z]{2}\d{5}", na=False)
    ]

    return {
        "rows_checked": len(df),
        "failed_count": len(failed_rows),
        "sample_failures": failed_rows.head(10).to_dict(orient="records")
    }


def infer_rule_action(rule_definition):

    rule_text = json.dumps(rule_definition).lower()
    
    if "executable_rule" in rule_definition:
        return rule_definition["executable_rule"]

    if "allowed_values" in rule_text or "valid values" in rule_text:
        if "a" in rule_text and "i" in rule_text:
            return {
                "type": "allowed_values",
                "values": ["A", "I"]
            }

    if "percentage" in rule_text or "%" in rule_text:
        if "0" in rule_text and "100" in rule_text:
            return {
                "type": "percentage_range",
                "min": 0,
                "max": 100
            }

    return {
        "type": "not_executable_yet"
    }

def evaluate_column_comparison(
    df,
    left_column,
    operator,
    right_column,
    null_behavior="ignore",
):
    left = df[left_column]
    right = df[right_column]

    if null_behavior == "ignore":
        valid_mask = left.notna() & right.notna()
    else:
        valid_mask = pd.Series(
            True,
            index=df.index
        )

    if operator == "==":
        pass_mask = left == right

    elif operator == "!=":
        pass_mask = left != right

    elif operator == "<":
        pass_mask = left < right

    elif operator == "<=":
        pass_mask = left <= right

    elif operator == ">":
        pass_mask = left > right

    elif operator == ">=":
        pass_mask = left >= right

    else:
        raise ValueError(
            f"Unsupported comparison operator: {operator}"
        )

    if null_behavior == "ignore":
        failed_mask = valid_mask & ~pass_mask
    else:
        failed_mask = ~pass_mask

    failed_rows = df.loc[
        failed_mask
    ]

    return {
        "failed_count": int(failed_mask.sum()),
        "sample_failures": (
            failed_rows[
                [left_column, right_column]
            ]
            .head(10)
            .to_dict(orient="records")
        ),
    }

def evaluate_conditional_required(
    df,
    condition_column,
    condition_operator,
    condition_value,
    required_column,
):
    if condition_operator != "==":
        raise ValueError(
            "conditional_required currently supports only ==."
        )

    condition_mask = (
        df[condition_column]
        .astype(str)
        == str(condition_value)
    )

    required_values = (
        df[required_column]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    failed_mask = (
        condition_mask
        & required_values.eq("")
    )

    failed_rows = df.loc[
        failed_mask
    ]

    return {
        "failed_count": int(failed_mask.sum()),
        "sample_failures": (
            failed_rows[
                [
                    condition_column,
                    required_column,
                ]
            ]
            .head(10)
            .to_dict(orient="records")
        ),
    }

def evaluate_at_least_one_present(
    df,
    columns,
):
    populated_masks = []

    for column in columns:
        populated = (
            df[column]
            .fillna("")
            .astype(str)
            .str.strip()
            .ne("")
        )

        populated_masks.append(
            populated
        )

    any_present = populated_masks[0]

    for mask in populated_masks[1:]:
        any_present = any_present | mask

    failed_mask = ~any_present

    failed_rows = df.loc[
        failed_mask
    ]

    return {
        "failed_count": int(failed_mask.sum()),
        "sample_failures": (
            failed_rows[
                columns
            ]
            .head(10)
            .to_dict(orient="records")
        ),
    }
    
def evaluate_columns_equal(
    df,
    columns,
    ignore_nulls=True,
):
    comparison_df = df[
        columns
    ].copy()

    if ignore_nulls:
        comparison_df = (
            comparison_df
            .replace("", pd.NA)
        )

        valid_mask = (
            comparison_df
            .notna()
            .all(axis=1)
        )
    else:
        valid_mask = pd.Series(
            True,
            index=df.index
        )

    first_column = columns[0]

    equal_mask = pd.Series(
        True,
        index=df.index
    )

    for column in columns[1:]:
        equal_mask = (
            equal_mask
            & (
                df[first_column]
                == df[column]
            )
        )

    failed_mask = (
        valid_mask
        & ~equal_mask
    )

    failed_rows = df.loc[
        failed_mask
    ]

    return {
        "failed_count": int(failed_mask.sum()),
        "sample_failures": (
            failed_rows[
                columns
            ]
            .head(10)
            .to_dict(orient="records")
        ),
    }
    
def main():

    with engine.begin() as conn:

        rules = conn.execute(
            text("""
                SELECT
                    id,
                    dataset_name,
                    column_name,
                    rule_type,
                    rule_scope,
                    target_columns,
                    rule_definition
                FROM dq.rule
                WHERE status = 'approved'
                  AND dataset_name = :dataset_name
                ORDER BY id
            """),
            {
                "dataset_name": DATASET_NAME
            }
        ).mappings().all()

        if not rules:
            print("No approved rules found.")
            return

        # Load the dataset once instead of once per rule.
        df = pd.read_sql(
            f'SELECT * FROM raw."{DATASET_NAME}"',
            engine
        )

        print(
            f"Loaded {len(df)} records from raw.{DATASET_NAME}"
        )

        for rule in rules:

            rule_id = rule["id"]
            dataset_name = rule["dataset_name"]
            column_name = rule["column_name"]

            rule_scope = (
                rule["rule_scope"]
                or "COLUMN"
            )

            rule_definition = rule["rule_definition"]

            # PostgreSQL JSONB normally returns a dictionary,
            # but handle text just in case.
            if isinstance(rule_definition, str):
                rule_definition = json.loads(
                    rule_definition
                )

            print()
            print(
                f"Evaluating rule {rule_id} "
                f"| Scope: {rule_scope}"
            )

            # =========================================================
            # ROW-SCOPE RULES
            # =========================================================

            if rule_scope == "ROW":

                action = rule_definition.get(
                    "executable_rule",
                    {}
                )

                action_type = action.get(
                    "type"
                )

                parameters = action.get(
                    "parameters",
                    {}
                )

                print(
                    f"ROW rule type: {action_type}"
                )

                # -----------------------------------------------------
                # column_comparison
                # -----------------------------------------------------

                if action_type == "column_comparison":

                    result_details = (
                        evaluate_column_comparison(
                            df,
                            left_column=parameters[
                                "left_column"
                            ],
                            operator=parameters[
                                "operator"
                            ],
                            right_column=parameters[
                                "right_column"
                            ],
                            null_behavior=parameters.get(
                                "null_behavior",
                                "ignore"
                            )
                        )
                    )

                # -----------------------------------------------------
                # conditional_required
                # -----------------------------------------------------

                elif action_type == "conditional_required":

                    result_details = (
                        evaluate_conditional_required(
                            df,
                            condition_column=parameters[
                                "condition_column"
                            ],
                            condition_operator=parameters[
                                "condition_operator"
                            ],
                            condition_value=parameters[
                                "condition_value"
                            ],
                            required_column=parameters[
                                "required_column"
                            ]
                        )
                    )

                # -----------------------------------------------------
                # at_least_one_present
                # -----------------------------------------------------

                elif action_type == "at_least_one_present":

                    result_details = (
                        evaluate_at_least_one_present(
                            df,
                            columns=parameters[
                                "columns"
                            ]
                        )
                    )

                # -----------------------------------------------------
                # columns_equal
                # -----------------------------------------------------

                elif action_type == "columns_equal":

                    result_details = (
                        evaluate_columns_equal(
                            df,
                            columns=parameters[
                                "columns"
                            ],
                            ignore_nulls=parameters.get(
                                "ignore_nulls",
                                True
                            )
                        )
                    )

                # -----------------------------------------------------
                # Unknown ROW rule
                # -----------------------------------------------------

                else:

                    result_details = {
                        "message": (
                            "ROW rule type is not executable yet."
                        ),
                        "rule_type": action_type,
                        "rule_definition": rule_definition
                    }

            # =========================================================
            # COLUMN-SCOPE RULES
            # =========================================================

            elif rule_scope == "COLUMN":

                print(
                    f"COLUMN rule: "
                    f"{dataset_name}.{column_name}"
                )

                # Keep using your existing helper for current
                # single-column rules.
                action = infer_rule_action(
                    rule_definition
                )

                action_type = action.get(
                    "type"
                )

                # -----------------------------------------------------
                # percentage_range
                # -----------------------------------------------------

                if action_type == "percentage_range":

                    result_details = (
                        evaluate_percentage_range(
                            df,
                            column_name,
                            min_value=action["min"],
                            max_value=action["max"]
                        )
                    )

                # -----------------------------------------------------
                # allowed_values
                # -----------------------------------------------------

                elif action_type == "allowed_values":

                    allowed_values = (
                        action.get("values")
                        or action.get(
                            "parameters",
                            {}
                        ).get("values")
                    )

                    result_details = (
                        evaluate_allowed_values(
                            df,
                            column_name,
                            allowed_values=allowed_values
                        )
                    )

                # -----------------------------------------------------
                # city_contains_state_or_zip
                # -----------------------------------------------------

                elif action_type == "city_contains_state_or_zip":

                    result_details = (
                        evaluate_city_contains_state_or_zip(
                            df,
                            city_column=action[
                                "city_column"
                            ]
                        )
                    )

                # -----------------------------------------------------
                # state_field_contains_zip
                # -----------------------------------------------------

                elif action_type == "state_field_contains_zip":

                    result_details = (
                        evaluate_state_field_contains_zip(
                            df,
                            state_column=action[
                                "state_column"
                            ]
                        )
                    )

                # -----------------------------------------------------
                # Unknown COLUMN rule
                # -----------------------------------------------------

                else:

                    result_details = {
                        "message": (
                            "COLUMN rule type is not executable yet."
                        ),
                        "rule_type": action_type,
                        "rule_definition": rule_definition
                    }

            # =========================================================
            # DATASET-SCOPE RULES
            # Future capability
            # =========================================================

            elif rule_scope == "DATASET":

                action = rule_definition.get(
                    "executable_rule",
                    {}
                )

                action_type = action.get(
                    "type"
                )

                parameters = action.get(
                    "parameters",
                    {}
                )

                print(
                    f"DATASET rule type: {action_type}"
                )

                # -----------------------------------------------------
                # Minimum Row Count
                # -----------------------------------------------------

                if action_type == "minimum_row_count":

                    result_details = (
                        evaluate_minimum_row_count(
                            df,
                            minimum_rows=parameters[
                                "minimum_rows"
                            ]
                        )
                    )

                # -----------------------------------------------------
                # Primary Key Unique
                # -----------------------------------------------------

                elif action_type == "primary_key_unique":

                    result_details = (
                        evaluate_primary_key_unique(
                            df,
                            column=parameters[
                                "column"
                            ]
                        )
                    )

                # -----------------------------------------------------
                # Column Combination Unique
                # -----------------------------------------------------

                elif action_type == "column_combination_unique":

                    result_details = (
                        evaluate_column_combination_unique(
                            df,
                            columns=parameters[
                                "columns"
                            ]
                        )
                    )

                # -----------------------------------------------------
                # Unsupported Dataset Rule
                # -----------------------------------------------------

                else:

                    result_details = {
                        "message": (
                            "DATASET rule type is not executable yet."
                        ),
                        "rule_type": action_type,
                        "rule_definition": rule_definition
                    }

            

            # =========================================================
            # Determine PASS / FAIL / SKIPPED
            # =========================================================

            if "failed_count" in result_details:

                result_status = (
                    "PASS"
                    if result_details["failed_count"] == 0
                    else "FAIL"
                )

            else:

                result_status = "SKIPPED"

                result_details[
                    "failed_count"
                ] = 0

            # =========================================================
            # Store result
            # =========================================================

            conn.execute(
                text("""
                    INSERT INTO dq.result
                    (
                        dataset_name,
                        rule_id,
                        result_status,
                        failed_count,
                        details
                    )
                    VALUES
                    (
                        :dataset_name,
                        :rule_id,
                        :result_status,
                        :failed_count,
                        CAST(:details AS jsonb)
                    )
                """),
                {
                    "dataset_name": dataset_name,
                    "rule_id": rule_id,
                    "result_status": result_status,
                    "failed_count": result_details.get(
                        "failed_count",
                        0
                    ),
                    "details": json.dumps(
                        result_details,
                        default=str
                    )
                }
            )

            print(
                f"Result: {result_status} | "
                f"Failures: "
                f"{result_details.get('failed_count', 0)}"
            )


if __name__ == "__main__":
    main()
