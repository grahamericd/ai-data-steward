import json
import re
import sys
import pandas as pd
from pathlib import Path

from sqlalchemy import text


# ============================================================
# Project imports
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import (
    LLM_MODEL,
    LLM_PROVIDER,
    engine,
)

from llm_client import (
    LLMError,
    generate_json,
)


# ============================================================
# Configuration
# ============================================================

MAX_CANDIDATES = 3
MIN_CONFIDENCE = 0.75
MIN_OBSERVED_SUPPORT = 0.95
MIN_COMPARABLE_ROWS = 25

SUPPORTED_RULE_TYPES = {
    "column_comparison",
    "conditional_required",
    "at_least_one_present",
    "columns_equal",
}

SUPPORTED_OPERATORS = {
    "==",
    "!=",
    "<",
    "<=",
    ">",
    ">=",
}


# ============================================================
# Command-line argument
# ============================================================

if len(sys.argv) != 2:
    print(
        "Usage: python generate_multicolumn_rules.py <dataset_name>"
    )
    sys.exit(1)

DATASET_NAME = sys.argv[1]


# ============================================================
# Metadata retrieval
# ============================================================

def get_dataset(conn, dataset_name):

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


def get_column_profiles(conn, dataset_name):

    return conn.execute(
        text("""
            SELECT
                column_name,
                row_count,
                null_count,
                null_percent,
                distinct_count,
                inferred_type,
                sample_values,
                min_value,
                max_value
            FROM metadata.column_profile
            WHERE dataset_name = :dataset_name
            ORDER BY column_name
        """),
        {
            "dataset_name": dataset_name,
        },
    ).mappings().all()


def get_existing_rules(conn, dataset_name):

    return conn.execute(
        text("""
            SELECT
                id,
                column_name,
                rule_scope,
                target_columns,
                rule_definition,
                status
            FROM dq.rule
            WHERE dataset_name = :dataset_name
              AND status NOT IN (
                  'rejected',
                  'retired'
              )
            ORDER BY id
        """),
        {
            "dataset_name": dataset_name,
        },
    ).mappings().all()


# ============================================================
# General helpers
# ============================================================

def normalize_name(name):

    return re.sub(
        r"[^a-z0-9]+",
        "_",
        str(name).lower(),
    ).strip("_")


def tokenize(name):

    ignored = {
        "field",
        "value",
        "record",
        "data",
        "the",
        "of",
    }

    return [
        token
        for token in normalize_name(name).split("_")
        if token
        and token not in ignored
    ]


def normalize_type(inferred_type):

    value = str(
        inferred_type or ""
    ).lower()

    if any(
        item in value
        for item in [
            "int",
            "float",
            "decimal",
            "numeric",
            "number",
        ]
    ):
        return "numeric"

    if any(
        item in value
        for item in [
            "date",
            "datetime",
            "timestamp",
        ]
    ):
        return "date"

    if any(
        item in value
        for item in [
            "bool",
            "boolean",
        ]
    ):
        return "boolean"

    return "text"


def trim_sample_values(
    value,
    max_values=4,
):

    if value is None:
        return []

    if isinstance(value, list):
        return value[:max_values]

    if isinstance(value, tuple):
        return list(
            value[:max_values]
        )

    if isinstance(value, str):

        try:
            parsed = json.loads(
                value
            )

            if isinstance(
                parsed,
                list,
            ):
                return parsed[
                    :max_values
                ]

        except json.JSONDecodeError:
            pass

        return [
            value[:200]
        ]

    return [
        str(value)[:200]
    ]


def compact_profile(profile):

    return {
        "column_name":
            profile["column_name"],

        "inferred_type":
            profile["inferred_type"],

        "null_percent":
            profile["null_percent"],

        "distinct_count":
            profile["distinct_count"],

        "sample_values":
            trim_sample_values(
                profile["sample_values"]
            ),
    }


# ============================================================
# Structural helpers
# ============================================================

def structural_name(column_name):
    """
    Generalize repeating numbered columns.

    owner_1_city -> owner_{n}_city
    owner_6_city -> owner_{n}_city
    """

    return re.sub(
        r"(^|_)\d+(_|$)",
        r"\1{n}\2",
        normalize_name(column_name),
    )


def structural_pair_signature(
    left_column,
    right_column,
    candidate_type,
):
    """
    Prevent owner_1 / owner_2 / owner_3 versions of the same
    relationship from all being sent to the LLM.
    """

    names = sorted(
        [
            structural_name(
                left_column
            ),
            structural_name(
                right_column
            ),
        ]
    )

    return (
        candidate_type,
        names[0],
        names[1],
    )


# ============================================================
# Candidate scoring
# ============================================================

def add_candidate(
    candidates,
    seen_signatures,
    *,
    candidate_type,
    left_column,
    right_column,
    score,
    reason,
    allowed_rule_types,
):

    if left_column == right_column:
        return

    signature = structural_pair_signature(
        left_column,
        right_column,
        candidate_type,
    )

    if signature in seen_signatures:
        return

    seen_signatures.add(
        signature
    )

    candidates.append(
        {
            "candidate_type":
                candidate_type,

            "left_column":
                left_column,

            "right_column":
                right_column,

            "score":
                score,

            "reason":
                reason,

            "allowed_rule_types":
                allowed_rule_types,
        }
    )


# ============================================================
# Candidate discovery
# ============================================================
def parse_date_series(series):
    """
    Parse common source-system date formats deterministically.

    Tries known formats before falling back to general parsing.
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
        "%m%d%Y",    # 06122026
        "%Y%m%d",    # 20260612
        "%m/%d/%Y",  # 06/12/2026
        "%Y-%m-%d",  # 2026-06-12
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



def analyze_date_relationship(
    df,
    left_column,
    right_column,
):
    """
    Empirically test both possible ordering relationships
    between two date-like columns.

    Returns the strongest observed relationship, or None if
    there is not enough evidence.
    """

    working = df[
        [
            left_column,
            right_column,
        ]
    ].copy()
    
    working[left_column] = parse_date_series(
        working[left_column]
    )

    working[right_column] = parse_date_series(
        working[right_column]
)

    # working[left_column] = pd.to_datetime(
        # working[left_column],
        # errors="coerce",
    # )

    # working[right_column] = pd.to_datetime(
        # working[right_column],
        # errors="coerce",
    # )

    comparable = working[
        working[left_column].notna()
        & working[right_column].notna()
    ]

    comparable_rows = len(
        comparable
    )

    if comparable_rows < MIN_COMPARABLE_ROWS:
        return None

    left_before_right = (
        comparable[left_column]
        <= comparable[right_column]
    )

    right_before_left = (
        comparable[right_column]
        <= comparable[left_column]
    )

    left_support = (
        left_before_right.sum()
        / comparable_rows
    )

    right_support = (
        right_before_left.sum()
        / comparable_rows
    )

    if left_support >= right_support:

        operator = "<="
        stronger_left = left_column
        stronger_right = right_column
        support = left_support
        failed_count = int(
            (~left_before_right).sum()
        )

    else:

        operator = "<="
        stronger_left = right_column
        stronger_right = left_column
        support = right_support
        failed_count = int(
            (~right_before_left).sum()
        )

    return {
        "left_column": stronger_left,
        "operator": operator,
        "right_column": stronger_right,
        "comparable_rows": comparable_rows,
        "passed_rows": (
            comparable_rows
            - failed_count
        ),
        "failed_rows": failed_count,
        "support": round(
            support,
            4,
        ),
    }



def discover_candidates(profiles):
    """
    Discover SPECIFIC pair relationships.

    Python narrows the search space before the LLM is called.
    """

    profile_map = {
        profile["column_name"]:
            profile
        for profile in profiles
    }

    columns = list(
        profile_map.keys()
    )

    candidates = []
    seen_signatures = set()

    # --------------------------------------------------------
    # DATE / DATE relationships
    # --------------------------------------------------------

    date_columns = [
        column
        for column in columns
        if (
            normalize_type(
                profile_map[
                    column
                ]["inferred_type"]
            )
            == "date"
            or "date"
            in normalize_name(
                column
            )
        )
    ]

    start_words = {
        "start",
        "begin",
        "file",
        "filing",
        "filed",
        "effective",
        "created",
        "creation",
        "registration",
    }

    end_words = {
        "end",
        "expire",
        "expiration",
        "cancel",
        "cancellation",
        "termination",
        "dissolution",
        "closed",
    }

    for left in date_columns:

        left_tokens = set(
            tokenize(left)
        )

        for right in date_columns:

            if left >= right:
                continue

            right_tokens = set(
                tokenize(right)
            )

            score = 0

            if (
                left_tokens
                & start_words
                and right_tokens
                & end_words
            ):
                score = 100

            elif (
                right_tokens
                & start_words
                and left_tokens
                & end_words
            ):
                score = 100

            elif (
                left_tokens
                & right_tokens
            ):
                score = 85

            if score:

                add_candidate(
                    candidates,
                    seen_signatures,
                    candidate_type=
                        "date_relationship",

                    left_column=left,
                    right_column=right,

                    score=score,

                    reason=(
                        "These columns appear to represent "
                        "related lifecycle dates."
                    ),

                    allowed_rule_types={
                        "column_comparison"
                    },
                )

    # --------------------------------------------------------
    # STATUS / DATE relationships
    # --------------------------------------------------------

    status_words = {
        "status",
        "active",
        "inactive",
        "cancel",
        "cancellation",
        "expire",
        "expiration",
        "dissolution",
        "termination",
    }

    status_columns = [
    column
    for column in columns
    if (
        set(tokenize(column))
        & status_words
    )
    and normalize_type(
        profile_map[
            column
        ]["inferred_type"]
    ) != "date"
    and "date" not in normalize_name(
        column
    )
]
    for status_column in status_columns:

        status_tokens = set(
            tokenize(
                status_column
            )
        )

        for date_column in date_columns:

            date_tokens = set(
                tokenize(
                    date_column
                )
            )

            shared = (
                status_tokens
                & date_tokens
            )

            if not shared:
                continue

            add_candidate(
                candidates,
                seen_signatures,
                candidate_type=
                    "status_date_relationship",

                left_column=
                    status_column,

                right_column=
                    date_column,

                score=95,

                reason=(
                    "A lifecycle status may determine "
                    "whether its related date is required."
                ),

                allowed_rule_types={
                    "conditional_required"
                },
            )

    # --------------------------------------------------------
    # IDENTIFIER alternatives
    # --------------------------------------------------------

    identifier_words = {
        "id",
        "identifier",
        "number",
        "document",
        "license",
        "registration",
        "fei",
        "ein",
        "ssn",
    }

    identifier_columns = [
        column
        for column in columns
        if set(
            tokenize(column)
        )
        & identifier_words
    ]

    for i, left in enumerate(
        identifier_columns
    ):

        left_tokens = set(
            tokenize(left)
        )

        for right in identifier_columns[
            i + 1:
        ]:

            right_tokens = set(
                tokenize(right)
            )

            # Require some semantic overlap.
            if not (
                left_tokens
                & right_tokens
            ):
                continue

            add_candidate(
                candidates,
                seen_signatures,
                candidate_type=
                    "identifier_relationship",

                left_column=left,
                right_column=right,

                score=75,

                reason=(
                    "These columns appear to represent "
                    "related identifiers."
                ),

                allowed_rule_types={
                    "at_least_one_present"
                },
            )

    # --------------------------------------------------------
    # True duplicate-semantic fields
    #
    # This is the ONLY candidate family allowed to propose
    # columns_equal.
    # --------------------------------------------------------

    for i, left in enumerate(
        columns
    ):

        left_structural = (
            structural_name(
                left
            )
        )

        left_type = normalize_type(
            profile_map[
                left
            ]["inferred_type"]
        )

        for right in columns[
            i + 1:
        ]:

            right_structural = (
                structural_name(
                    right
                )
            )

            right_type = normalize_type(
                profile_map[
                    right
                ]["inferred_type"]
            )

            if left_type != right_type:
                continue

            # Only treat fields as equality candidates when their
            # generalized semantic names are actually identical.
            #
            # Example:
            # owner_1_state vs owner_2_state
            #
            # NOT:
            # owner_1_city vs owner_1_address

            if (
                left_structural
                != right_structural
            ):
                continue

            add_candidate(
                candidates,
                seen_signatures,
                candidate_type=
                    "duplicate_semantic_field",

                left_column=left,
                right_column=right,

                score=70,

                reason=(
                    "The columns have the same generalized "
                    "semantic field name."
                ),

                allowed_rule_types={
                    "columns_equal"
                },
            )

    # --------------------------------------------------------
    # Rank candidates
    # --------------------------------------------------------

    candidates.sort(
        key=lambda item:
            item["score"],
        reverse=True,
    )

    return candidates[
        :MAX_CANDIDATES
    ]


# ============================================================
# Existing rule signatures
# ============================================================

def executable_signature(
    executable_rule,
):

    return json.dumps(
        executable_rule,
        sort_keys=True,
        default=str,
    )


def existing_rule_signatures(
    existing_rules,
):

    signatures = set()

    for rule in existing_rules:

        definition = rule[
            "rule_definition"
        ]

        if isinstance(
            definition,
            str,
        ):

            try:
                definition = (
                    json.loads(
                        definition
                    )
                )

            except json.JSONDecodeError:
                continue

        if not isinstance(
            definition,
            dict,
        ):
            continue

        executable_rule = (
            definition.get(
                "executable_rule"
            )
        )

        if executable_rule:

            signatures.add(
                executable_signature(
                    executable_rule
                )
            )

    return signatures


# ============================================================
# Prompt
# ============================================================

def build_candidate_prompt(
    dataset,
    candidate,
    profile_map,
):

    left_column = candidate[
        "left_column"
    ]

    right_column = candidate[
        "right_column"
    ]

    profiles = [
        compact_profile(
            profile_map[
                left_column
            ]
        ),

        compact_profile(
            profile_map[
                right_column
            ]
        ),
    ]

    allowed_rule_types = sorted(
        candidate[
            "allowed_rule_types"
        ]
    )

    return f"""
You are a senior enterprise data steward.

Evaluate ONE proposed relationship between exactly TWO columns.

Do not search for unrelated rules.

Dataset:
{dataset['dataset_name']}

Dataset description:
{dataset.get('description')}

Candidate relationship type:
{candidate['candidate_type']}

Why this candidate was selected:
{candidate['reason']}

Empirical evidence from the actual dataset:
{json.dumps(
    candidate.get("empirical_evidence"),
    indent=2,
    default=str
)}

Columns:
{json.dumps(profiles, indent=2, default=str)}

Allowed executable rule types for THIS candidate:
{json.dumps(allowed_rule_types)}

Your job:

Determine whether the available column names, inferred types, null patterns,
and sample values provide sufficient evidence for a deterministic,
reusable business data quality relationship.

IMPORTANT:

- Do not invent business requirements.
- Related fields do NOT automatically have to be populated.
- Fields belonging to the same entity do NOT automatically have to be equal.
- Address, city, state, ZIP, and country fields should NOT be equal simply
  because they describe the same location.
- If the evidence is not strong enough, return accepted=false.
- Do not explain anything outside the JSON object.
- Do not generate SQL.
- Only use one of the allowed executable rule types.

Return exactly:

{{
  "accepted": false,
  "business_definition": "",
  "confidence_score": 0.0,
  "evidence": "",
  "target_columns": [],
  "executable_rule": {{
    "type": "",
    "parameters": {{}}
  }}
}}

If no defensible relationship exists:

{{
  "accepted": false,
  "business_definition": "",
  "confidence_score": 0.0,
  "evidence": "",
  "target_columns": [],
  "executable_rule": {{
    "type": "none",
    "parameters": {{}}
  }}
}}

If empirical evidence is supplied, treat it as observed evidence from
the actual dataset.

Do not accept the rule solely because current records happen to follow
the pattern. Accept it only if the observed relationship also makes
semantic business sense based on the field meanings.

When empirical evidence is present:

- Treat observed support above 95% as strong statistical evidence.
- Treat observed support above 99% across at least 100 comparable rows as very strong evidence.
- Do not reject a candidate merely because the relationship was not explicitly stated as a business policy.
- Reject only when the proposed relationship is semantically implausible, unsupported by field meaning, or likely coincidental.
- The purpose of this review is to determine whether the empirically observed relationship is reasonable enough to propose for human steward approval.

Return valid JSON only.
"""


# ============================================================
# Guardrails
# ============================================================

def referenced_columns(
    executable_rule,
):

    rule_type = executable_rule.get(
        "type"
    )

    parameters = executable_rule.get(
        "parameters",
        {}
    )

    if rule_type == "column_comparison":

        return [
            parameters.get(
                "left_column"
            ),
            parameters.get(
                "right_column"
            ),
        ]

    if rule_type == "conditional_required":

        return [
            parameters.get(
                "condition_column"
            ),
            parameters.get(
                "required_column"
            ),
        ]

    if rule_type in {
        "at_least_one_present",
        "columns_equal",
    }:

        return parameters.get(
            "columns",
            []
        )

    return []


def validate_candidate_response(
    response,
    candidate,
    profile_map,
):

    if not isinstance(
        response,
        dict,
    ):

        return (
            False,
            "Response is not a JSON object.",
            None,
        )

    if not response.get(
        "accepted",
        False,
    ):

        return (
            False,
            "LLM rejected candidate.",
            None,
        )

    try:

        confidence = float(
            response.get(
                "confidence_score",
                0,
            )
        )

    except (
        TypeError,
        ValueError,
    ):

        return (
            False,
            "Invalid confidence score.",
            None,
        )

    if confidence < MIN_CONFIDENCE:

        return (
            False,
            (
                "Confidence below threshold: "
                f"{confidence}"
            ),
            None,
        )

    executable_rule = response.get(
        "executable_rule",
        {}
    )

    rule_type = executable_rule.get(
        "type"
    )

    if rule_type not in SUPPORTED_RULE_TYPES:

        return (
            False,
            (
                "Unsupported rule type: "
                f"{rule_type}"
            ),
            None,
        )

    if rule_type not in candidate[
        "allowed_rule_types"
    ]:

        return (
            False,
            (
                f"Rule type {rule_type} is not "
                "allowed for this candidate."
            ),
            None,
        )

    columns = [
        column
        for column in referenced_columns(
            executable_rule
        )
        if column
    ]

    expected_columns = {
        candidate[
            "left_column"
        ],
        candidate[
            "right_column"
        ],
    }

    if set(columns) != expected_columns:

        return (
            False,
            (
                "Rule does not reference exactly "
                "the candidate columns."
            ),
            None,
        )

    # --------------------------------------------------------
    # Column comparison validation
    # --------------------------------------------------------

    if rule_type == "column_comparison":

        parameters = executable_rule[
            "parameters"
        ]

        operator = parameters.get(
            "operator"
        )

        if operator not in SUPPORTED_OPERATORS:

            return (
                False,
                (
                    "Unsupported comparison "
                    f"operator: {operator}"
                ),
                None,
            )

        left = parameters.get(
            "left_column"
        )

        right = parameters.get(
            "right_column"
        )

        left_type = normalize_type(
            profile_map[
                left
            ]["inferred_type"]
        )

        right_type = normalize_type(
            profile_map[
                right
            ]["inferred_type"]
        )

        if left_type != right_type:

            return (
                False,
                (
                    "Column comparison uses "
                    "incompatible types."
                ),
                None,
            )

        if (
            operator
            in {
                "<",
                "<=",
                ">",
                ">=",
            }
            and left_type
            not in {
                "numeric",
                "date",
            }
        ):

            return (
                False,
                (
                    "Ordered comparisons require "
                    "numeric or date columns."
                ),
                None,
            )

    # --------------------------------------------------------
    # Conditional required validation
    # --------------------------------------------------------

    if rule_type == "conditional_required":

        parameters = executable_rule[
            "parameters"
        ]

        if parameters.get(
            "condition_operator"
        ) != "==":

            return (
                False,
                (
                    "conditional_required supports "
                    "only ==."
                ),
                None,
            )

        if "condition_value" not in parameters:

            return (
                False,
                (
                    "conditional_required is missing "
                    "condition_value."
                ),
                None,
            )

    # --------------------------------------------------------
    # At least one present
    # --------------------------------------------------------

    if rule_type == "at_least_one_present":

        parameters = executable_rule[
            "parameters"
        ]

        if not isinstance(
            parameters.get(
                "columns"
            ),
            list,
        ):

            return (
                False,
                "columns parameter must be a list.",
                None,
            )

    # --------------------------------------------------------
    # Columns equal
    # --------------------------------------------------------

    if rule_type == "columns_equal":

        # Critical semantic guardrail:
        # only candidates created as duplicate semantic fields
        # may ever become equality rules.

        if (
            candidate[
                "candidate_type"
            ]
            != "duplicate_semantic_field"
        ):

            return (
                False,
                (
                    "columns_equal rejected because "
                    "candidate fields are not semantic duplicates."
                ),
                None,
            )

    cleaned_rule = {
        "business_definition":
            response.get(
                "business_definition",
                ""
            ),

        "confidence_score":
            confidence,

        "evidence":
            response.get(
                "evidence",
                ""
            ),

        "target_columns":
            columns,

        "executable_rule":
            executable_rule,
    }

    return (
        True,
        None,
        cleaned_rule,
    )


# ============================================================
# Insert rule
# ============================================================

def insert_rule(
    conn,
    dataset_name,
    rule,
):

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
                'llm_generated',
                CAST(:rule_definition AS jsonb),
                'proposed',
                'ROW',
                CAST(:target_columns AS jsonb),
                :llm_provider,
                :llm_model,
                :prompt_version
            )
        """),
        {
            "dataset_name":
                dataset_name,

            "rule_definition":
                json.dumps(
                    rule
                ),

            "target_columns":
                json.dumps(
                    rule[
                        "target_columns"
                    ]
                ),

            "llm_provider":
                LLM_PROVIDER,

            "llm_model":
                LLM_MODEL,

            "prompt_version":
                "multicolumn-pair-v4",
        },
    )


# ============================================================
# Main
# ============================================================

def main():

    print(
        f"Analyzing multi-column relationships "
        f"for dataset: {DATASET_NAME}"
    )

    print(
        f"LLM provider: {LLM_PROVIDER}"
    )

    print(
        f"LLM model: {LLM_MODEL}"
    )

    # --------------------------------------------------------
    # Load metadata
    # --------------------------------------------------------

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

        profiles = get_column_profiles(
            conn,
            DATASET_NAME,
        )

        if not profiles:

            raise ValueError(
                f"No column profiles exist for "
                f"{DATASET_NAME}. "
                "Run profiling first."
            )

        existing_rules = get_existing_rules(
            conn,
            DATASET_NAME,
        )

    profile_map = {
        profile["column_name"]:
            profile
        for profile in profiles
    }

    raw_schema = dataset.get(
        "raw_schema",
        "raw"
    )

    raw_table = dataset.get(
        "raw_table",
        DATASET_NAME
    )

    df = pd.read_sql(
        f'SELECT * FROM "{raw_schema}"."{raw_table}"',
        engine
    )

    print(
        f"Raw rows available for empirical analysis: "
        f"{len(df)}"
    )

    # --------------------------------------------------------
    # Discover candidates
    # --------------------------------------------------------

    candidates = discover_candidates(
        profiles
    )

    print()
    print(
        f"Columns analyzed: "
        f"{len(profiles)}"
    )

    print(
        f"Relationship candidates selected: "
        f"{len(candidates)}"
    )

    if not candidates:

        print(
            "No strong multi-column candidates "
            "were discovered."
        )

        return

    print()
    print(
        "Candidate ranking:"
    )

    for index, candidate in enumerate(
        candidates,
        start=1,
    ):

        print(
            f"{index}. "
            f"Score {candidate['score']} | "
            f"{candidate['candidate_type']} | "
            f"{candidate['left_column']} <-> "
            f"{candidate['right_column']}"
        )

    existing_signatures = (
        existing_rule_signatures(
            existing_rules
        )
    )

    newly_inserted_signatures = set()

    total_accepted = 0
    total_rejected = 0
    total_inserted = 0
    total_duplicates = 0
    total_llm_errors = 0

    # --------------------------------------------------------
    # Analyze each candidate
    # --------------------------------------------------------

    with engine.begin() as conn:

        for number, candidate in enumerate(
            candidates,
            start=1,
        ):

            print()
            print(
                "----------------------------------------"
            )

            print(
                f"Candidate "
                f"{number}/{len(candidates)}"
            )

            print(
                f"Score: "
                f"{candidate['score']}"
            )

            print(
                f"Type: "
                f"{candidate['candidate_type']}"
            )

            print(
                f"Columns: "
                f"{candidate['left_column']} "
                f"<-> "
                f"{candidate['right_column']}"
            )

            print(
                f"Reason: "
                f"{candidate['reason']}"
            )
###########
            empirical_evidence = None

            if candidate["candidate_type"] == "date_relationship":

                empirical_evidence = analyze_date_relationship(
                    df,
                    candidate["left_column"],
                    candidate["right_column"],
                )

                if empirical_evidence is None:

                    print(
                        "Candidate skipped: "
                        "not enough comparable date records."
                    )

                    continue

                print(
                    "Observed relationship:"
                )

                print(
                    f"  {empirical_evidence['left_column']} "
                    f"{empirical_evidence['operator']} "
                    f"{empirical_evidence['right_column']}"
                )

                print(
                    f"  Comparable rows: "
                    f"{empirical_evidence['comparable_rows']}"
                )

                print(
                    f"  Passed rows: "
                    f"{empirical_evidence['passed_rows']}"
                )

                print(
                    f"  Failed rows: "
                    f"{empirical_evidence['failed_rows']}"
                )

                print(
                    f"  Observed support: "
                    f"{empirical_evidence['support']:.2%}"
                )

                if (
                    empirical_evidence["support"]
                    < MIN_OBSERVED_SUPPORT
                ):

                    print(
                        "Candidate skipped: "
                        f"observed support below "
                        f"{MIN_OBSERVED_SUPPORT:.0%}."
                    )

                    continue

                # Replace the original pair ordering with the
                # empirically supported ordering.
                candidate = dict(
                    candidate
                )

                candidate[
                    "left_column"
                ] = empirical_evidence[
                    "left_column"
                ]

                candidate[
                    "right_column"
                ] = empirical_evidence[
                    "right_column"
                ]

                candidate[
                    "empirical_evidence"
                ] = empirical_evidence


            prompt = build_candidate_prompt(
                dataset,
                candidate,
                profile_map,
            )

            # ------------------------------------------------
            # LLM call
            # ------------------------------------------------

            try:

                response = generate_json(
                    prompt
                )

            except LLMError as exc:

                total_llm_errors += 1

                print(
                    f"LLM error: {exc}"
                )

                continue

            # ------------------------------------------------
            # Guardrails
            # ------------------------------------------------

            valid, reason, rule = (
                validate_candidate_response(
                    response,
                    candidate,
                    profile_map,
                )
            )

            if not valid:

                total_rejected += 1

                print(
                    f"Candidate rejected: "
                    f"{reason}"
                )

                continue

            total_accepted += 1

            signature = (
                executable_signature(
                    rule[
                        "executable_rule"
                    ]
                )
            )

            if (
                signature
                in existing_signatures
                or signature
                in newly_inserted_signatures
            ):

                total_duplicates += 1

                print(
                    "Duplicate rule skipped."
                )

                continue

            # ------------------------------------------------
            # Insert
            # ------------------------------------------------

            insert_rule(
                conn,
                DATASET_NAME,
                rule,
            )

            newly_inserted_signatures.add(
                signature
            )

            total_inserted += 1

            print(
                "Inserted proposed ROW rule:"
            )

            print(
                f"  Type: "
                f"{rule['executable_rule']['type']}"
            )

            print(
                f"  Columns: "
                f"{rule['target_columns']}"
            )

            print(
                f"  Confidence: "
                f"{rule['confidence_score']}"
            )

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------

    print()
    print(
        "========================================"
    )

    print(
        "Multi-column analysis complete"
    )

    print(
        "========================================"
    )

    print(
        f"Columns analyzed: "
        f"{len(profiles)}"
    )

    print(
        f"Candidates sent to LLM: "
        f"{len(candidates)}"
    )

    print(
        f"LLM errors: "
        f"{total_llm_errors}"
    )

    print(
        f"Candidates accepted: "
        f"{total_accepted}"
    )

    print(
        f"Candidates rejected: "
        f"{total_rejected}"
    )

    print(
        f"Rules inserted: "
        f"{total_inserted}"
    )

    print(
        f"Duplicates skipped: "
        f"{total_duplicates}"
    )


if __name__ == "__main__":
    main()