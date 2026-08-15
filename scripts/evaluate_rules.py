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
    extract_executable_rule,
    get_rule_spec,
    validate_executable_rule,
)
from stewardship_context import get_stewardship_run_id


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


class FailureSamples(list):
    """Display samples carrying the complete identifier set internally."""

    def __init__(self, records, row_identifiers):
        super().__init__(records)
        self.row_identifiers = row_identifiers


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

    if primary_key and primary_key in failed_rows.columns:
        identifier_values = failed_rows[primary_key]
    elif "_dq_row_identifier" in failed_rows.columns:
        identifier_values = failed_rows["_dq_row_identifier"]
    else:
        identifier_values = pd.Series(
            [f"index:{index}" for index in failed_rows.index],
            index=failed_rows.index,
        )

    row_identifiers = []
    for index, value in identifier_values.items():
        if pd.notna(value):
            row_identifiers.append(str(value))
        elif "_dq_row_identifier" in failed_rows.columns:
            row_identifiers.append(str(failed_rows.at[index, "_dq_row_identifier"]))
        else:
            row_identifiers.append(f"index:{index}")

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

    records = (
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

    return FailureSamples(records, row_identifiers)


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


def _quote_reference_identifier(value):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(value or "")):
        raise ValueError(f"Invalid reference identifier: {value!r}")
    return f'"{value}"'


def find_matching_reference_keys(
    reference_dataset,
    reference_columns,
    source_keys,
    case_sensitive=False,
):
    """Return only source keys present in a registered reference dataset."""

    if not source_keys:
        return set()

    with engine.begin() as conn:
        registration = conn.execute(
            text("""
                SELECT schema_name, table_name, key_columns
                FROM metadata.reference_dataset
                WHERE reference_dataset_name = :name AND active = TRUE
            """),
            {"name": reference_dataset},
        ).mappings().first()
        if registration is None:
            raise ValueError(
                f"Reference dataset is not registered or active: {reference_dataset}"
            )

        allowed_columns = registration["key_columns"]
        if isinstance(allowed_columns, str):
            allowed_columns = json.loads(allowed_columns)
        if not set(reference_columns).issubset(set(allowed_columns or [])):
            raise ValueError(
                f"Reference columns {reference_columns} are not registered keys "
                f"for {reference_dataset}."
            )

        schema = _quote_reference_identifier(registration["schema_name"])
        table_name = _quote_reference_identifier(registration["table_name"])
        aliases = [f"v{index}" for index in range(len(reference_columns))]
        record_columns = ", ".join(f'"{alias}" text' for alias in aliases)
        comparisons = []
        for alias, reference_column in zip(aliases, reference_columns):
            left = f'r.{_quote_reference_identifier(reference_column)}::text'
            right = f'x."{alias}"'
            comparisons.append(
                f"{left} = {right}"
                if case_sensitive
                else f"LOWER({left}) = LOWER({right})"
            )
        select_values = ", ".join(f'x."{alias}"' for alias in aliases)
        payload = [
            {alias: value for alias, value in zip(aliases, key)}
            for key in source_keys
        ]
        rows = conn.execute(
            text(f"""
                WITH incoming AS
                (
                    SELECT *
                    FROM jsonb_to_recordset(CAST(:payload AS jsonb))
                    AS x({record_columns})
                )
                SELECT DISTINCT {select_values}
                FROM incoming x
                INNER JOIN {schema}.{table_name} r
                    ON {' AND '.join(comparisons)}
            """),
            {"payload": json.dumps(payload)},
        ).all()

    return {tuple(str(value) for value in row) for row in rows}


def evaluate_reference_combination(
    df,
    *,
    reference_dataset,
    column_mapping,
    case_sensitive=False,
    ignore_nulls=True,
    primary_key=None,
    **kwargs,
):
    """Validate source column combinations against authoritative reference keys."""

    if not column_mapping:
        raise ValueError("column_mapping must not be empty.")
    source_columns = list(column_mapping)
    reference_columns = [column_mapping[column] for column in source_columns]
    for column in source_columns:
        if column not in df.columns:
            raise ValueError(f"Column not found: {column}")

    normalized = pd.DataFrame(index=df.index)
    for column in source_columns:
        normalized[column] = normalize_blank_series(df[column])
    populated_mask = normalized.notna().all(axis=1) & normalized.ne("").all(axis=1)

    source_keys = list(
        dict.fromkeys(
            tuple(str(value) for value in row)
            for row in normalized.loc[populated_mask, source_columns].itertuples(
                index=False, name=None
            )
        )
    )
    matches = find_matching_reference_keys(
        reference_dataset,
        reference_columns,
        source_keys,
        case_sensitive=case_sensitive,
    )
    if not case_sensitive:
        matches = {tuple(value.lower() for value in key) for key in matches}

    def row_matches(row):
        key = tuple(str(value) for value in row)
        if not case_sensitive:
            key = tuple(value.lower() for value in key)
        return key in matches

    matched_mask = pd.Series(False, index=df.index)
    matched_mask.loc[populated_mask] = [
        row_matches(row)
        for row in normalized.loc[populated_mask, source_columns].itertuples(
            index=False, name=None
        )
    ]
    failed_mask = populated_mask & ~matched_mask
    if not ignore_nulls:
        failed_mask = failed_mask | ~populated_mask

    return {
        "failed_count": int(failed_mask.sum()),
        "reference_dataset": reference_dataset,
        "sample_failures": sample_failure_records(
            df,
            failed_mask,
            columns=source_columns,
            primary_key=primary_key,
        ),
    }


def evaluate_reference_value(
    df,
    column_name,
    *,
    reference_dataset,
    reference_column,
    case_sensitive=False,
    primary_key=None,
    **kwargs,
):
    return evaluate_reference_combination(
        df,
        reference_dataset=reference_dataset,
        column_mapping={column_name: reference_column},
        case_sensitive=case_sensitive,
        ignore_nulls=True,
        primary_key=primary_key,
    )


def evaluate_city_state_zip_reference(
    df,
    *,
    city_column,
    state_column,
    zip_column,
    reference_dataset="us_zip_codes",
    case_sensitive=False,
    ignore_nulls=True,
    primary_key=None,
    **kwargs,
):
    return evaluate_reference_combination(
        df,
        reference_dataset=reference_dataset,
        column_mapping={
            city_column: "place_name",
            state_column: "state_code",
            zip_column: "zip_code",
        },
        case_sensitive=case_sensitive,
        ignore_nulls=ignore_nulls,
        primary_key=primary_key,
    )


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

    "evaluate_reference_value":
        evaluate_reference_value,

    "evaluate_column_comparison":
        evaluate_column_comparison,

    "evaluate_conditional_required":
        evaluate_conditional_required,

    "evaluate_at_least_one_present":
        evaluate_at_least_one_present,

    "evaluate_columns_equal":
        evaluate_columns_equal,

    "evaluate_reference_combination":
        evaluate_reference_combination,

    "evaluate_city_state_zip_reference":
        evaluate_city_state_zip_reference,

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

    stored_details = dict(result_details)
    row_identifiers = stored_details.pop(
        "_failed_row_identifiers",
        [],
    )

    failed_count = int(
        stored_details.get(
            "failed_count",
            0,
        )
    )

    with engine.begin() as conn:

        result_id = conn.execute(
            text("""
                INSERT INTO dq.result
                (
                    dataset_name,
                    rule_id,
                    result_status,
                    failed_count,
                    details
                    ,stewardship_run_id
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
                    ,:stewardship_run_id
                )
                RETURNING id
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
                        stored_details,
                        default=str,
                    ),
                "stewardship_run_id": get_stewardship_run_id(),
            },
        ).scalar_one()

        if row_identifiers:
            conn.execute(
                text("""
                    INSERT INTO dq.failed_record
                    (
                        result_id,
                        rule_id,
                        dataset_name,
                        source_row_identifier
                    )
                    VALUES
                    (
                        :result_id,
                        :rule_id,
                        :dataset_name,
                        :source_row_identifier
                    )
                """),
                [
                    {
                        "result_id": result_id,
                        "rule_id": rule_id,
                        "dataset_name": dataset_name,
                        "source_row_identifier": identifier,
                    }
                    for identifier in row_identifiers
                ],
            )

    return result_id


def evaluate_rule(rule, df, primary_key=None):
    """Evaluate one rule without allowing its failure to escape the boundary."""

    rule_scope = rule.get("rule_scope") or "COLUMN"
    column_name = rule.get("column_name")
    rule_type = rule.get("rule_type")

    try:
        executable_rule = extract_executable_rule(rule.get("rule_definition"))
        valid, reason, executable_rule = validate_executable_rule(
            executable_rule,
            expected_scope=rule_scope,
        )
        if not valid:
            raise ValueError(f"Registry validation failed: {reason}")

        rule_type = executable_rule["type"]
        parameters = executable_rule["parameters"]
        spec = get_rule_spec(rule_type)

        if spec is None or not spec.executor:
            raise ValueError(f"Rule '{rule_type}' has no registered executor.")

        executor = EXECUTOR_FUNCTIONS.get(spec.executor)
        if executor is None:
            raise ValueError(
                f"Registered executor '{spec.executor}' is not implemented."
            )

        if rule_scope == "COLUMN":
            if not column_name:
                raise ValueError("COLUMN rule does not have a column_name.")
            details = executor(
                df,
                column_name,
                primary_key=primary_key,
                **parameters,
            )
        else:
            details = executor(
                df,
                primary_key=primary_key,
                **parameters,
            )

        if not isinstance(details, dict):
            raise TypeError("Rule executor must return a details JSON object.")
        if "failed_count" not in details:
            raise ValueError("Rule executor result is missing failed_count.")

        failed_count = details["failed_count"]
        if (
            isinstance(failed_count, bool)
            or not isinstance(failed_count, int)
            or failed_count < 0
        ):
            raise ValueError(
                "Rule executor failed_count must be a non-negative integer."
            )

        samples = details.get("sample_failures")
        if isinstance(samples, FailureSamples):
            details["sample_failures"] = list(samples)
            details["_failed_row_identifiers"] = samples.row_identifiers

        return ("PASS" if failed_count == 0 else "FAIL"), details

    except Exception as exc:
        return "ERROR", {
            "failed_count": 0,
            "evaluation_failed": True,
            "error_type": type(exc).__name__,
            "message": str(exc),
            "rule_type": rule_type,
            "rule_scope": rule_scope,
            "column_name": column_name,
        }


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
            f'SELECT *, ctid::text AS "_dq_row_identifier" '
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

        result_status, result_details = evaluate_rule(
            rule,
            df,
            primary_key=primary_key,
        )

        if result_status == "PASS":
            pass_count += 1
        elif result_status == "FAIL":
            fail_count += 1
        else:
            error_count += 1

        # ====================================================
        # Persist result
        # ====================================================

        try:
            write_result(
                dataset_name=dataset_name,
                rule_id=rule_id,
                result_status=result_status,
                result_details=result_details,
            )
        except Exception as persistence_error:
            # Each write owns its transaction. A persistence failure for one
            # result cannot roll back prior results or stop later rules.
            print(
                f"Could not persist Rule {rule_id} result: "
                f"{type(persistence_error).__name__}: {persistence_error}"
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
