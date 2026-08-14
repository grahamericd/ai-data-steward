import json
import re
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import text


# ============================================================
# Project imports
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import engine

from rule_registry import (
    get_rule_spec,
    validate_executable_rule,
)


# ============================================================
# Command-line argument
# ============================================================

# if len(sys.argv) != 2:
    # print(
        # "Usage: python evaluate_rules.py <dataset_name>"
    # )
    # sys.exit(1)

# DATASET_NAME = sys.argv[1]


# ============================================================
# General helpers
# ============================================================

def normalize_blank_series(series):
    """
    Convert values to trimmed strings while preserving null detection.
    """

    return (
        series
        .astype("string")
        .str.strip()
    )


def parse_date_series(series):
    """
    Parse common date formats used by source datasets.

    This intentionally tries known formats rather than asking
    pandas to infer every value independently.
    """

    cleaned = (
        series
        .astype("string")
        .str.strip()
        .replace(
            {
                "": pd.NA,
                "None": pd.NA,
                "nan": pd.NA,
                "NaN": pd.NA,
            }
        )
    )

    result = pd.Series(
        pd.NaT,
        index=cleaned.index,
        dtype="datetime64[ns]",
    )

    formats = [
        "%m%d%Y",
        "%Y%m%d",
        "%m/%d/%Y",
        "%Y-%m-%d",
        "%m-%d-%Y",
    ]

    for date_format in formats:

        remaining = (
            result.isna()
            & cleaned.notna()
        )

        if not remaining.any():
            break

        parsed = pd.to_datetime(
            cleaned.loc[remaining],
            format=date_format,
            errors="coerce",
        )

        result.loc[remaining] = parsed

    return result


def sample_failure_records(
    df,
    failed_mask,
    columns=None,
    primary_key=None,
    limit=10,
):
    """
    Return a small JSON-safe sample of failed records.
    """

    failed_rows = df.loc[
        failed_mask
    ]

    selected_columns = []

    if (
        primary_key
        and primary_key in df.columns
    ):
        selected_columns.append(
            primary_key
        )

    for column in columns or []:

        if (
            column in df.columns
            and column not in selected_columns
        ):
            selected_columns.append(
                column
            )

    if selected_columns:

        failed_rows = failed_rows[
            selected_columns
        ]

    return (
        failed_rows
        .head(limit)
        .where(
            pd.notna(
                failed_rows
            ),
            None,
        )
        .to_dict(
            orient="records"
        )
    )


# ============================================================
# COLUMN rule evaluators
# ============================================================

def evaluate_allowed_values(
    df,
    column_name,
    *,
    values,
    primary_key=None,
    **kwargs,
):
    """
    Value must be one of the configured allowed values.
    Nulls are ignored by this rule.
    """

    if column_name not in df.columns:
        raise ValueError(
            f"Column not found: {column_name}"
        )

    series = df[
        column_name
    ]

    populated_mask = series.notna()

    failed_mask = (
        populated_mask
        & ~series.isin(values)
    )

    return {
        "failed_count": int(
            failed_mask.sum()
        ),
        "sample_failures":
            sample_failure_records(
                df,
                failed_mask,
                columns=[
                    column_name
                ],
                primary_key=primary_key,
            ),
    }


def evaluate_not_null(
    df,
    column_name,
    *,
    primary_key=None,
    **kwargs,
):
    """
    Value must not be NULL or blank.
    """

    if column_name not in df.columns:
        raise ValueError(
            f"Column not found: {column_name}"
        )

    series = normalize_blank_series(
        df[column_name]
    )

    failed_mask = (
        series.isna()
        | series.eq("")
    )

    return {
        "failed_count": int(
            failed_mask.sum()
        ),
        "sample_failures":
            sample_failure_records(
                df,
                failed_mask,
                columns=[
                    column_name
                ],
                primary_key=primary_key,
            ),
    }


def evaluate_max_length(
    df,
    column_name,
    *,
    max_length,
    primary_key=None,
    **kwargs,
):
    """
    Non-null text values must not exceed max_length.
    """

    if column_name not in df.columns:
        raise ValueError(
            f"Column not found: {column_name}"
        )

    series = normalize_blank_series(
        df[column_name]
    )

    populated_mask = (
        series.notna()
        & series.ne("")
    )

    failed_mask = (
        populated_mask
        & (
            series.str.len()
            > max_length
        )
    )

    return {
        "failed_count": int(
            failed_mask.sum()
        ),
        "sample_failures":
            sample_failure_records(
                df,
                failed_mask,
                columns=[
                    column_name
                ],
                primary_key=primary_key,
            ),
    }


def evaluate_min_length(
    df,
    column_name,
    *,
    min_length,
    primary_key=None,
    **kwargs,
):
    """
    Non-null text values must meet min_length.
    """

    if column_name not in df.columns:
        raise ValueError(
            f"Column not found: {column_name}"
        )

    series = normalize_blank_series(
        df[column_name]
    )

    populated_mask = (
        series.notna()
        & series.ne("")
    )

    failed_mask = (
        populated_mask
        & (
            series.str.len()
            < min_length
        )
    )

    return {
        "failed_count": int(
            failed_mask.sum()
        ),
        "sample_failures":
            sample_failure_records(
                df,
                failed_mask,
                columns=[
                    column_name
                ],
                primary_key=primary_key,
            ),
    }


def evaluate_regex(
    df,
    column_name,
    *,
    pattern,
    primary_key=None,
    **kwargs,
):
    """
    Non-null values must match the configured regular expression.
    """

    if column_name not in df.columns:
        raise ValueError(
            f"Column not found: {column_name}"
        )

    # Validate regex before evaluating records.
    re.compile(
        pattern
    )

    series = normalize_blank_series(
        df[column_name]
    )

    populated_mask = (
        series.notna()
        & series.ne("")
    )

    matches = (
        series
        .fillna("")
        .str.fullmatch(
            pattern,
            na=False,
        )
    )

    failed_mask = (
        populated_mask
        & ~matches
    )

    return {
        "failed_count": int(
            failed_mask.sum()
        ),
        "sample_failures":
            sample_failure_records(
                df,
                failed_mask,
                columns=[
                    column_name
                ],
                primary_key=primary_key,
            ),
    }


def evaluate_numeric_range(
    df,
    column_name,
    *,
    min,
    max,
    primary_key=None,
    **kwargs,
):
    """
    Numeric values must fall within the inclusive configured range.
    """

    if column_name not in df.columns:
        raise ValueError(
            f"Column not found: {column_name}"
        )

    original = df[
        column_name
    ]

    numeric = pd.to_numeric(
        original,
        errors="coerce",
    )

    populated_mask = (
        original.notna()
        & normalize_blank_series(
            original
        ).ne("")
    )

    invalid_numeric_mask = (
        populated_mask
        & numeric.isna()
    )

    outside_range_mask = (
        numeric.notna()
        & (
            (numeric < min)
            | (numeric > max)
        )
    )

    failed_mask = (
        invalid_numeric_mask
        | outside_range_mask
    )

    return {
        "failed_count": int(
            failed_mask.sum()
        ),
        "sample_failures":
            sample_failure_records(
                df,
                failed_mask,
                columns=[
                    column_name
                ],
                primary_key=primary_key,
            ),
    }


def evaluate_percentage_range(
    df,
    column_name,
    *,
    min,
    max,
    primary_key=None,
    **kwargs,
):
    """
    Percentage rule uses the same inclusive numeric-range behavior.
    """

    return evaluate_numeric_range(
        df,
        column_name,
        min=min,
        max=max,
        primary_key=primary_key,
    )


def evaluate_date_format(
    df,
    column_name,
    *,
    format,
    primary_key=None,
    **kwargs,
):
    """
    Validate that populated values conform exactly to a date format.
    """

    if column_name not in df.columns:
        raise ValueError(
            f"Column not found: {column_name}"
        )

    format_map = {
        "MMDDYYYY": "%m%d%Y",
        "YYYYMMDD": "%Y%m%d",
        "MM/DD/YYYY": "%m/%d/%Y",
        "YYYY-MM-DD": "%Y-%m-%d",
        "MM-DD-YYYY": "%m-%d-%Y",
    }

    python_format = format_map.get(
        format,
        format,
    )

    series = normalize_blank_series(
        df[column_name]
    )

    populated_mask = (
        series.notna()
        & series.ne("")
    )

    parsed = pd.to_datetime(
        series,
        format=python_format,
        errors="coerce",
    )

    failed_mask = (
        populated_mask
        & parsed.isna()
    )

    return {
        "failed_count": int(
            failed_mask.sum()
        ),
        "sample_failures":
            sample_failure_records(
                df,
                failed_mask,
                columns=[
                    column_name
                ],
                primary_key=primary_key,
            ),
    }


def evaluate_city_contains_state_or_zip(
    df,
    column_name=None,
    *,
    city_column=None,
    primary_key=None,
    **kwargs,
):
    """
    Detect city fields that appear to contain state or ZIP data.
    """

    target_column = (
        city_column
        or column_name
    )

    if target_column not in df.columns:
        raise ValueError(
            f"Column not found: {target_column}"
        )

    series = (
        normalize_blank_series(
            df[target_column]
        )
        .fillna("")
    )

    # ZIP anywhere in the city field.
    contains_zip = series.str.contains(
        r"\b\d{5}(?:-\d{4})?\b",
        regex=True,
        na=False,
    )

    # Common pattern such as:
    # TALLAHASSEE FL
    # TALLAHASSEE, FL
    #
    # This checks for a trailing two-character uppercase token.
    contains_state_suffix = series.str.contains(
        r"(?:,\s*|\s+)[A-Z]{2}\s*$",
        regex=True,
        na=False,
    )

    failed_mask = (
        contains_zip
        | contains_state_suffix
    )

    return {
        "failed_count": int(
            failed_mask.sum()
        ),
        "sample_failures":
            sample_failure_records(
                df,
                failed_mask,
                columns=[
                    target_column
                ],
                primary_key=primary_key,
            ),
    }


def evaluate_state_field_contains_zip(
    df,
    column_name=None,
    *,
    state_column=None,
    primary_key=None,
    **kwargs,
):
    """
    Detect state fields contaminated with ZIP-code data.
    """

    target_column = (
        state_column
        or column_name
    )

    if target_column not in df.columns:
        raise ValueError(
            f"Column not found: {target_column}"
        )

    series = (
        normalize_blank_series(
            df[target_column]
        )
        .fillna("")
    )

    failed_mask = series.str.contains(
        r"\d{5}(?:-\d{4})?",
        regex=True,
        na=False,
    )

    return {
        "failed_count": int(
            failed_mask.sum()
        ),
        "sample_failures":
            sample_failure_records(
                df,
                failed_mask,
                columns=[
                    target_column
                ],
                primary_key=primary_key,
            ),
    }


# ============================================================
# ROW / multi-column evaluators
# ============================================================

def evaluate_column_comparison(
    df,
    *,
    left_column,
    operator,
    right_column,
    null_behavior="ignore",
    primary_key=None,
    **kwargs,
):
    """
    Compare two columns using a deterministic operator.
    """

    for column in [
        left_column,
        right_column,
    ]:
        if column not in df.columns:
            raise ValueError(
                f"Column not found: {column}"
            )

    left = df[
        left_column
    ]

    right = df[
        right_column
    ]

    # Date relationships discovered by the empirical engine
    # commonly use names ending in _date.
    if (
        "date" in left_column.lower()
        and "date" in right_column.lower()
    ):

        left = parse_date_series(
            left
        )

        right = parse_date_series(
            right
        )

    else:

        # If both columns can be meaningfully converted to
        # numeric values, compare numerically.
        left_numeric = pd.to_numeric(
            left,
            errors="coerce",
        )

        right_numeric = pd.to_numeric(
            right,
            errors="coerce",
        )

        left_populated = left.notna()
        right_populated = right.notna()

        left_numeric_rate = (
            left_numeric.notna().sum()
            / max(
                int(
                    left_populated.sum()
                ),
                1,
            )
        )

        right_numeric_rate = (
            right_numeric.notna().sum()
            / max(
                int(
                    right_populated.sum()
                ),
                1,
            )
        )

        if (
            left_numeric_rate >= 0.95
            and right_numeric_rate >= 0.95
        ):
            left = left_numeric
            right = right_numeric

    comparable_mask = (
        left.notna()
        & right.notna()
    )

    if operator == "==":

        pass_mask = (
            left == right
        )

    elif operator == "!=":

        pass_mask = (
            left != right
        )

    elif operator == "<":

        pass_mask = (
            left < right
        )

    elif operator == "<=":

        pass_mask = (
            left <= right
        )

    elif operator == ">":

        pass_mask = (
            left > right
        )

    elif operator == ">=":

        pass_mask = (
            left >= right
        )

    else:

        raise ValueError(
            f"Unsupported comparison operator: {operator}"
        )

    if null_behavior == "ignore":

        failed_mask = (
            comparable_mask
            & ~pass_mask
        )

    elif null_behavior == "fail":

        failed_mask = (
            ~comparable_mask
            | (
                comparable_mask
                & ~pass_mask
            )
        )

    else:

        raise ValueError(
            f"Unsupported null_behavior: {null_behavior}"
        )

    return {
        "failed_count": int(
            failed_mask.sum()
        ),
        "comparable_count": int(
            comparable_mask.sum()
        ),
        "sample_failures":
            sample_failure_records(
                df,
                failed_mask,
                columns=[
                    left_column,
                    right_column,
                ],
                primary_key=primary_key,
            ),
    }


def evaluate_conditional_required(
    df,
    *,
    condition_column,
    condition_operator,
    condition_value,
    required_column,
    primary_key=None,
    **kwargs,
):
    """
    Require required_column when condition_column satisfies
    the configured condition.
    """

    for column in [
        condition_column,
        required_column,
    ]:
        if column not in df.columns:
            raise ValueError(
                f"Column not found: {column}"
            )

    if condition_operator != "==":
        raise ValueError(
            "conditional_required currently supports only ==."
        )

    condition_series = (
        normalize_blank_series(
            df[
                condition_column
            ]
        )
    )

    required_series = (
        normalize_blank_series(
            df[
                required_column
            ]
        )
    )

    condition_mask = (
        condition_series
        == str(
            condition_value
        )
    )

    missing_required = (
        required_series.isna()
        | required_series.eq("")
    )

    failed_mask = (
        condition_mask
        & missing_required
    )

    return {
        "failed_count": int(
            failed_mask.sum()
        ),
        "condition_match_count": int(
            condition_mask.sum()
        ),
        "sample_failures":
            sample_failure_records(
                df,
                failed_mask,
                columns=[
                    condition_column,
                    required_column,
                ],
                primary_key=primary_key,
            ),
    }


def evaluate_at_least_one_present(
    df,
    *,
    columns,
    primary_key=None,
    **kwargs,
):
    """
    At least one field in the configured set must be populated.
    """

    for column in columns:

        if column not in df.columns:
            raise ValueError(
                f"Column not found: {column}"
            )

    populated_masks = []

    for column in columns:

        series = normalize_blank_series(
            df[column]
        )

        populated_masks.append(
            (
                series.notna()
                & series.ne("")
            )
        )

    any_present = populated_masks[
        0
    ].copy()

    for mask in populated_masks[
        1:
    ]:

        any_present = (
            any_present
            | mask
        )

    failed_mask = (
        ~any_present
    )

    return {
        "failed_count": int(
            failed_mask.sum()
        ),
        "sample_failures":
            sample_failure_records(
                df,
                failed_mask,
                columns=columns,
                primary_key=primary_key,
            ),
    }


def evaluate_columns_equal(
    df,
    *,
    columns,
    ignore_nulls=True,
    primary_key=None,
    **kwargs,
):
    """
    All configured columns must contain equal values.
    """

    for column in columns:

        if column not in df.columns:
            raise ValueError(
                f"Column not found: {column}"
            )

    comparison = df[
        columns
    ].copy()

    for column in columns:

        comparison[
            column
        ] = normalize_blank_series(
            comparison[
                column
            ]
        )

    if ignore_nulls:

        valid_mask = (
            comparison
            .notna()
            .all(axis=1)
        )

    else:

        valid_mask = pd.Series(
            True,
            index=df.index,
        )

    first_column = columns[
        0
    ]

    equal_mask = pd.Series(
        True,
        index=df.index,
    )

    for column in columns[
        1:
    ]:

        equal_mask = (
            equal_mask
            & (
                comparison[
                    first_column
                ]
                == comparison[
                    column
                ]
            )
        )

    failed_mask = (
        valid_mask
        & ~equal_mask
    )

    return {
        "failed_count": int(
            failed_mask.sum()
        ),
        "sample_failures":
            sample_failure_records(
                df,
                failed_mask,
                columns=columns,
                primary_key=primary_key,
            ),
    }


# ============================================================
# DATASET evaluators
# ============================================================

def evaluate_minimum_row_count(
    df,
    *,
    minimum_rows,
    **kwargs,
):
    """
    Dataset must contain at least minimum_rows records.
    """

    actual_rows = len(
        df
    )

    return {
        "failed_count": (
            0
            if actual_rows >= minimum_rows
            else 1
        ),
        "actual_row_count":
            actual_rows,
        "minimum_row_count":
            minimum_rows,
    }


def evaluate_primary_key_unique(
    df,
    *,
    column,
    primary_key=None,
    **kwargs,
):
    """
    Registered primary-key values must be unique.
    """

    if column not in df.columns:
        raise ValueError(
            f"Column not found: {column}"
        )

    duplicate_mask = (
        df[
            column
        ]
        .duplicated(
            keep=False
        )
    )

    return {
        "failed_count": int(
            duplicate_mask.sum()
        ),
        "sample_failures":
            sample_failure_records(
                df,
                duplicate_mask,
                columns=[
                    column
                ],
                primary_key=primary_key,
            ),
    }


def evaluate_column_combination_unique(
    df,
    *,
    columns,
    primary_key=None,
    **kwargs,
):
    """
    A configured combination of columns must uniquely
    identify records.
    """

    for column in columns:

        if column not in df.columns:
            raise ValueError(
                f"Column not found: {column}"
            )

    duplicate_mask = (
        df
        .duplicated(
            subset=columns,
            keep=False,
        )
    )

    return {
        "failed_count": int(
            duplicate_mask.sum()
        ),
        "sample_failures":
            sample_failure_records(
                df,
                duplicate_mask,
                columns=columns,
                primary_key=primary_key,
            ),
    }


# ============================================================
# Registry executor mapping
#
# rule_registry.py owns the association:
#
# rule type -> executor NAME
#
# This file owns the actual Python function objects.
# ============================================================

EXECUTOR_FUNCTIONS = {

    "evaluate_allowed_values":
        evaluate_allowed_values,

    "evaluate_not_null":
        evaluate_not_null,

    "evaluate_max_length":
        evaluate_max_length,

    "evaluate_min_length":
        evaluate_min_length,

    "evaluate_regex":
        evaluate_regex,

    "evaluate_numeric_range":
        evaluate_numeric_range,

    "evaluate_percentage_range":
        evaluate_percentage_range,

    "evaluate_date_format":
        evaluate_date_format,

    "evaluate_city_contains_state_or_zip":
        evaluate_city_contains_state_or_zip,

    "evaluate_state_field_contains_zip":
        evaluate_state_field_contains_zip,

    "evaluate_column_comparison":
        evaluate_column_comparison,

    "evaluate_conditional_required":
        evaluate_conditional_required,

    "evaluate_at_least_one_present":
        evaluate_at_least_one_present,

    "evaluate_columns_equal":
        evaluate_columns_equal,

    "evaluate_minimum_row_count":
        evaluate_minimum_row_count,

    "evaluate_primary_key_unique":
        evaluate_primary_key_unique,

    "evaluate_column_combination_unique":
        evaluate_column_combination_unique,
}


# ============================================================
# Rule-definition compatibility
# ============================================================

def extract_executable_rule(
    rule_definition,
):
    """
    Extract executable_rule from current rule JSON.

    Also provides limited compatibility with older rules that
    stored parameters alongside type rather than under parameters.
    """

    if isinstance(
        rule_definition,
        str,
    ):

        rule_definition = json.loads(
            rule_definition
        )

    if not isinstance(
        rule_definition,
        dict,
    ):

        raise ValueError(
            "rule_definition must be a JSON object."
        )

    executable_rule = (
        rule_definition.get(
            "executable_rule"
        )
    )

    if executable_rule is None:

        raise ValueError(
            "rule_definition does not contain executable_rule."
        )

    if not isinstance(
        executable_rule,
        dict,
    ):

        raise ValueError(
            "executable_rule must be a JSON object."
        )

    # --------------------------------------------------------
    # Legacy compatibility
    #
    # Older rules occasionally looked like:
    #
    # {
    #     "type": "percentage_range",
    #     "min": 0,
    #     "max": 100
    # }
    #
    # Convert that to the canonical contract.
    # --------------------------------------------------------

    if (
        "parameters"
        not in executable_rule
    ):

        rule_type = executable_rule.get(
            "type"
        )

        parameters = {
            key: value
            for key, value
            in executable_rule.items()
            if key != "type"
        }

        executable_rule = {
            "type":
                rule_type,

            "parameters":
                parameters,
        }

    return executable_rule


# ============================================================
# Result persistence
# ============================================================

def write_result(
    dataset_name,
    rule_id,
    result_status,
    result_details,
):
    """
    Write one evaluation result in its own transaction.

    This prevents one bad rule from rolling back successful
    results written for earlier rules.
    """

    failed_count = int(
        result_details.get(
            "failed_count",
            0,
        )
    )

    with engine.begin() as conn:

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
                    CAST(
                        :details
                        AS jsonb
                    )
                )
            """),
            {
                "dataset_name":
                    dataset_name,

                "rule_id":
                    rule_id,

                "result_status":
                    result_status,

                "failed_count":
                    failed_count,

                "details":
                    json.dumps(
                        result_details,
                        default=str,
                    ),
            },
        )


# ============================================================
# Main
# ============================================================

def main(dataset_name):

    print(
        f"Evaluating approved rules for: "
        f"{dataset_name}"
    )

    # --------------------------------------------------------
    # Load registered dataset metadata
    # --------------------------------------------------------

    with engine.begin() as conn:

        dataset = conn.execute(
            text("""
                SELECT *
                FROM metadata.dataset
                WHERE dataset_name = :dataset_name
                  AND active = TRUE
            """),
            {
                "dataset_name":
                    dataset_name
            },
        ).mappings().first()

        if dataset is None:

            raise ValueError(
                f"Dataset not found or inactive: "
                f"{dataset_name}"
            )

        dataset = dict(
            dataset
        )

        rules = conn.execute(
            text("""
                SELECT
                    id,
                    dataset_name,
                    column_name,
                    rule_type,
                    rule_scope,
                    target_columns,
                    rule_definition,
                    status
                FROM dq.rule
                WHERE dataset_name = :dataset_name
                  AND status = 'approved'
                ORDER BY id
            """),
            {
                "dataset_name":
                    dataset_name
            },
        ).mappings().all()

    if not rules:

        print(
            "No approved rules found."
        )

        return

    # --------------------------------------------------------
    # Resolve actual raw table
    # --------------------------------------------------------

    raw_schema = (
        dataset.get(
            "raw_schema"
        )
        or "raw"
    )

    raw_table = (
        dataset.get(
            "raw_table"
        )
        or dataset_name
    )

    primary_key = dataset.get(
        "primary_key"
    )

    # --------------------------------------------------------
    # Load dataset ONCE
    #
    # This fixes the previous per-rule reload behavior.
    # --------------------------------------------------------

    df = pd.read_sql(
        (
            f'SELECT * '
            f'FROM "{raw_schema}"."{raw_table}"'
        ),
        engine,
    )

    print(
        f"Loaded {len(df)} records from "
        f"{raw_schema}.{raw_table}"
    )

    print(
        f"Approved rules found: "
        f"{len(rules)}"
    )

    pass_count = 0
    fail_count = 0
    error_count = 0

    # ========================================================
    # Evaluate rules
    # ========================================================

    for rule in rules:

        rule_id = rule[
            "id"
        ]

        column_name = rule[
            "column_name"
        ]

        rule_scope = (
            rule[
                "rule_scope"
            ]
            or "COLUMN"
        )

        rule_definition = rule[
            "rule_definition"
        ]

        print()
        print(
            "----------------------------------------"
        )

        print(
            f"Evaluating Rule {rule_id}"
        )

        print(
            f"Scope: {rule_scope}"
        )

        if column_name:

            print(
                f"Column: {column_name}"
            )

        try:

            # ------------------------------------------------
            # Extract canonical executable rule
            # ------------------------------------------------

            executable_rule = (
                extract_executable_rule(
                    rule_definition
                )
            )

            # ------------------------------------------------
            # Registry validation
            # ------------------------------------------------

            valid, reason, executable_rule = (
                validate_executable_rule(
                    executable_rule,
                    expected_scope=
                        rule_scope,
                )
            )

            if not valid:

                raise ValueError(
                    "Registry validation failed: "
                    f"{reason}"
                )

            rule_type = executable_rule[
                "type"
            ]

            parameters = executable_rule[
                "parameters"
            ]

            print(
                f"Rule type: {rule_type}"
            )

            # ------------------------------------------------
            # Get specification from central registry
            # ------------------------------------------------

            spec = get_rule_spec(
                rule_type
            )

            if spec is None:

                raise ValueError(
                    f"No registry specification "
                    f"found for rule: "
                    f"{rule_type}"
                )

            if not spec.executor:

                raise ValueError(
                    f"Rule '{rule_type}' has "
                    "no registered executor."
                )

            # ------------------------------------------------
            # Resolve actual executor function
            # ------------------------------------------------

            executor = (
                EXECUTOR_FUNCTIONS.get(
                    spec.executor
                )
            )

            if executor is None:

                raise ValueError(
                    f"Registered executor "
                    f"'{spec.executor}' "
                    "is not implemented in "
                    "evaluate_rules.py."
                )

            # ------------------------------------------------
            # Execute
            # ------------------------------------------------

            if rule_scope == "COLUMN":

                if not column_name:

                    raise ValueError(
                        "COLUMN rule does not have "
                        "a column_name."
                    )

                result_details = executor(
                    df,
                    column_name,
                    primary_key=
                        primary_key,
                    **parameters,
                )

            else:

                result_details = executor(
                    df,
                    primary_key=
                        primary_key,
                    **parameters,
                )

            # ------------------------------------------------
            # Determine result
            # ------------------------------------------------

            failed_count = int(
                result_details.get(
                    "failed_count",
                    0,
                )
            )

            result_status = (
                "PASS"
                if failed_count == 0
                else "FAIL"
            )

            if result_status == "PASS":

                pass_count += 1

            else:

                fail_count += 1

        # ====================================================
        # Rule-level error isolation
        # ====================================================

        except Exception as exc:

            result_status = (
                "ERROR"
            )

            error_count += 1

            result_details = {
                "failed_count": 0,

                "error_type":
                    type(exc).__name__,

                "message":
                    str(exc),

                "rule_scope":
                    rule_scope,

                "column_name":
                    column_name,
            }

        # ====================================================
        # Persist result
        # ====================================================

        write_result(
            dataset_name=
                dataset_name,

            rule_id=
                rule_id,

            result_status=
                result_status,

            result_details=
                result_details,
        )

        print(
            f"Result: {result_status}"
        )

        print(
            f"Failures: "
            f"{result_details.get('failed_count', 0)}"
        )

        if result_status == "ERROR":

            print(
                f"Error: "
                f"{result_details.get('message')}"
            )

    # ========================================================
    # Summary
    # ========================================================

    print()
    print(
        "========================================"
    )

    print(
        "Rule evaluation complete"
    )

    print(
        "========================================"
    )

    print(
        f"PASS: {pass_count}"
    )

    print(
        f"FAIL: {fail_count}"
    )

    print(
        f"ERROR: {error_count}"
    )

    print(
        f"TOTAL: "
        f"{pass_count + fail_count + error_count}"
    )


if __name__ == "__main__":

    if len(sys.argv) != 2:
        print(
            "Usage: python evaluate_rules.py <dataset_name>"
        )
        sys.exit(1)

    main(
        sys.argv[1]
    )