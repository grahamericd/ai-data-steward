import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
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

from rule_registry import (
    get_rule_types,
    validate_executable_rule,
)
from stewardship_context import get_stewardship_run_id
from scripts.load_dataset import quote_identifier
# ============================================================
# Configuration
# ============================================================

LOCAL_MAX_CANDIDATES = int(os.getenv("MULTICOLUMN_OLLAMA_MAX_CANDIDATES", "3"))
API_MAX_CANDIDATES = int(os.getenv("MULTICOLUMN_API_MAX_CANDIDATES", "12"))
API_MAX_WORKERS = int(os.getenv("MULTICOLUMN_API_MAX_WORKERS", "4"))
EMPIRICAL_SAMPLE_ROWS = int(os.getenv("MULTICOLUMN_SAMPLE_ROWS", "10000"))
MIN_CONFIDENCE = 0.75
MIN_OBSERVED_SUPPORT = 0.95
MIN_COMPARABLE_ROWS = 25

# SUPPORTED_RULE_TYPES = {
    # "column_comparison",
    # "conditional_required",
    # "at_least_one_present",
    # "columns_equal",
# }
#
# SUPPORTED_OPERATORS = {
    # "==",
    # "!=",
    # "<",
    # "<=",
    # ">",
    # ">=",
# }


def candidate_limit(provider=LLM_PROVIDER):
    """Keep local inference bounded while allowing configured APIs more work."""
    return LOCAL_MAX_CANDIDATES if provider == "ollama" else API_MAX_CANDIDATES


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


class CandidateAnalysisCache:
    """Cache normalized column series reused across candidate families."""

    def __init__(self, df):
        self.df = df
        self._present = {}
        self._dates = {}

    def present(self, column):
        if column not in self._present:
            values = self.df[column].astype("string").str.strip()
            self._present[column] = values.notna() & ~values.isin(
                ["", "None", "nan", "NaN"]
            )
        return self._present[column]

    def dates(self, column):
        if column not in self._dates:
            self._dates[column] = parse_date_series(self.df[column])
        return self._dates[column]


def _support_evidence(passed, comparable, **details):
    comparable_rows = int(comparable.sum())
    if comparable_rows < MIN_COMPARABLE_ROWS:
        return None
    passed_rows = int((passed & comparable).sum())
    return {
        **details,
        "comparable_rows": comparable_rows,
        "passed_rows": passed_rows,
        "failed_rows": comparable_rows - passed_rows,
        "support": round(passed_rows / comparable_rows, 4),
    }


def analyze_at_least_one_present(cache, left_column, right_column):
    left = cache.present(left_column)
    right = cache.present(right_column)
    comparable = pd.Series(True, index=left.index)
    return _support_evidence(
        left | right,
        comparable,
        columns=[left_column, right_column],
        relationship="at_least_one_present",
    )


def analyze_conditional_completeness(cache, condition_column, required_column):
    """Find a categorical value that strongly implies required-field presence."""
    raw_condition = cache.df[condition_column].astype("string").str.strip()
    required = cache.present(required_column)
    non_null = raw_condition.notna() & (raw_condition != "")
    value_counts = raw_condition.loc[non_null].value_counts().head(25)
    best = None
    for value, count in value_counts.items():
        if int(count) < MIN_COMPARABLE_ROWS:
            continue
        comparable = non_null & (raw_condition == value)
        evidence = _support_evidence(
            required,
            comparable,
            condition_column=condition_column,
            condition_operator="==",
            condition_value=str(value),
            required_column=required_column,
            relationship="conditional_required",
        )
        if evidence and (best is None or evidence["support"] > best["support"]):
            best = evidence
    return best


def analyze_candidate(df, candidate, cache=None):
    """Run the deterministic support test appropriate for a candidate family."""
    cache = cache or CandidateAnalysisCache(df)
    family = candidate["candidate_type"]
    left = candidate["left_column"]
    right = candidate["right_column"]
    if family == "date_relationship":
        return analyze_date_relationship(df, left, right)
    if family == "identifier_relationship":
        return analyze_at_least_one_present(cache, left, right)
    if family in {
        "status_date_relationship",
        "conditional_completeness",
        "repeated_structure_completeness",
    }:
        forward = analyze_conditional_completeness(cache, left, right)
        if family == "status_date_relationship":
            return forward
        reverse = analyze_conditional_completeness(cache, right, left)
        choices = [item for item in (forward, reverse) if item]
        return max(choices, key=lambda item: item["support"]) if choices else None
    if family == "duplicate_semantic_field":
        comparable = cache.present(left) & cache.present(right)
        equal = cache.df[left].astype("string").str.strip() == cache.df[right].astype("string").str.strip()
        return _support_evidence(
            equal,
            comparable,
            columns=[left, right],
            relationship="columns_equal",
        )
    return None


def screen_candidates(df, candidates, provider=LLM_PROVIDER):
    """Empirically screen and rank candidates before spending LLM calls."""
    cache = CandidateAnalysisCache(df)
    screened = []
    diagnostics = {"discovered": len(candidates), "insufficient_data": 0, "low_support": 0}
    for original in candidates:
        candidate = dict(original)
        evidence = analyze_candidate(df, candidate, cache)
        if evidence is None:
            diagnostics["insufficient_data"] += 1
            continue
        if evidence["support"] < MIN_OBSERVED_SUPPORT:
            diagnostics["low_support"] += 1
            continue
        candidate["empirical_evidence"] = evidence
        if evidence.get("left_column"):
            candidate["left_column"] = evidence["left_column"]
            candidate["right_column"] = evidence["right_column"]
        candidate["score"] += round(evidence["support"] * 10, 2)
        screened.append(candidate)
    screened.sort(key=lambda item: (item["score"], item["empirical_evidence"]["comparable_rows"]), reverse=True)
    diagnostics["supported"] = len(screened)
    limit = candidate_limit(provider)
    diagnostics["selected_for_llm"] = min(len(screened), limit)
    diagnostics["capacity_skipped"] = max(0, len(screened) - limit)
    return screened[:limit], diagnostics


def review_candidates(dataset, candidates, profile_map, provider=LLM_PROVIDER, generator=generate_json):
    """Review locally in series or use bounded concurrency for API providers."""
    def review(candidate):
        try:
            return generator(build_candidate_prompt(dataset, candidate, profile_map)), None
        except LLMError as exc:
            return None, exc

    workers = 1 if provider == "ollama" else max(1, min(API_MAX_WORKERS, len(candidates)))
    if workers == 1:
        return [review(candidate) for candidate in candidates]
    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(review, candidates))



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
    # CONDITIONAL COMPLETENESS
    # Categorical type/status fields may make a related detail required.
    # --------------------------------------------------------

    condition_words = {"type", "status", "category", "class", "kind", "indicator", "flag"}
    categorical_columns = [
        column for column in columns
        if set(tokenize(column)) & condition_words
        and 1 < int(profile_map[column].get("distinct_count") or 0) <= 25
    ]
    for condition_column in categorical_columns:
        condition_context = set(tokenize(condition_column)) - condition_words
        for required_column in columns:
            if required_column == condition_column:
                continue
            required_tokens = set(tokenize(required_column))
            if condition_context and not condition_context.intersection(required_tokens):
                continue
            if not condition_context and not set(tokenize(condition_column)).intersection(required_tokens):
                continue
            add_candidate(
                candidates,
                seen_signatures,
                candidate_type="conditional_completeness",
                left_column=condition_column,
                right_column=required_column,
                score=78,
                reason="A categorical field value may conditionally require its related detail field.",
                allowed_rule_types={"conditional_required"},
            )

    # --------------------------------------------------------
    # REPEATED STRUCTURES
    # owner_1_type/owner_1_name are screened for directional
    # completeness; repeated slots are never assumed equal.
    # --------------------------------------------------------

    repeated = {}
    for column in columns:
        match = re.match(r"^(.*?)[_](\d+)[_](.+)$", normalize_name(column))
        if match:
            repeated.setdefault((match.group(1), match.group(2)), []).append(column)
    for group_columns in repeated.values():
        for index, left in enumerate(group_columns):
            for right in group_columns[index + 1:]:
                add_candidate(
                    candidates,
                    seen_signatures,
                    candidate_type="repeated_structure_completeness",
                    left_column=left,
                    right_column=right,
                    score=76,
                    reason="Fields occupy the same repeated entity slot and may have a completeness dependency.",
                    allowed_rule_types={"conditional_required"},
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

    return candidates


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

Perform semantic review of the empirically supported relationship. Python has
already measured support; do not reinterpret the statistics or invent different
parameters. Decide only whether the observed relationship is a plausible,
reusable business data quality rule worthy of steward review.

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
- The executable parameters must exactly reproduce the empirical relationship.

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
    """
    Validate an LLM response for a multi-column candidate.

    Validation layers:

    1. Confirm the LLM accepted the candidate.
    2. Enforce confidence threshold.
    3. Validate executable_rule against the central rule registry.
    4. Confirm the rule is allowed for this candidate type.
    5. Confirm the rule references exactly the candidate columns.
    6. Apply candidate-specific semantic/type guardrails.
    7. Return a cleaned rule ready for insertion into dq.rule.
    """

    # ============================================================
    # Basic response validation
    # ============================================================

    if not isinstance(response, dict):
        return (
            False,
            "LLM response is not a JSON object.",
            None,
        )

    # ------------------------------------------------------------
    # Candidate acceptance
    # ------------------------------------------------------------

    if not response.get("accepted", False):
        return (
            False,
            "LLM rejected candidate.",
            None,
        )

    # ------------------------------------------------------------
    # Confidence score
    # ------------------------------------------------------------

    try:
        confidence = float(
            response.get(
                "confidence_score",
                0,
            )
        )

    except (TypeError, ValueError):
        return (
            False,
            "confidence_score must be numeric.",
            None,
        )

    if not 0.0 <= confidence <= 1.0:
        return (
            False,
            (
                "confidence_score must be between "
                "0.0 and 1.0."
            ),
            None,
        )

    if confidence < MIN_CONFIDENCE:
        return (
            False,
            (
                f"Confidence below threshold: "
                f"{confidence:.2f} "
                f"< {MIN_CONFIDENCE:.2f}"
            ),
            None,
        )

    # ============================================================
    # Executable rule
    # ============================================================

    executable_rule = response.get(
        "executable_rule"
    )

    if not isinstance(
        executable_rule,
        dict,
    ):
        return (
            False,
            "Response is missing executable_rule.",
            None,
        )

    # ------------------------------------------------------------
    # Central Rule Registry validation
    #
    # THIS replaces the old local:
    #
    # SUPPORTED_RULE_TYPES
    # SUPPORTED_OPERATORS
    #
    # validation.
    # ------------------------------------------------------------

    valid, reason, cleaned_executable_rule = (
        validate_executable_rule(
            executable_rule,
            expected_scope="ROW",
        )
    )

    if not valid:
        return (
            False,
            (
                "Rule registry validation failed: "
                f"{reason}"
            ),
            None,
        )

    executable_rule = (
        cleaned_executable_rule
    )

    rule_type = executable_rule[
        "type"
    ]

    parameters = executable_rule[
        "parameters"
    ]

    # ============================================================
    # Candidate-specific rule restriction
    # ============================================================

    allowed_rule_types = candidate.get(
        "allowed_rule_types",
        set(),
    )

    if rule_type not in allowed_rule_types:
        return (
            False,
            (
                f"Rule type '{rule_type}' is valid "
                "in the registry, but is not allowed "
                "for this candidate type "
                f"'{candidate.get('candidate_type')}'."
            ),
            None,
        )

    # ============================================================
    # Determine referenced columns
    # ============================================================

    columns = [
        column
        for column in referenced_columns(
            executable_rule
        )
        if column
    ]

    # Remove duplicates while preserving order.

    columns = list(
        dict.fromkeys(columns)
    )

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
                "Rule must reference exactly the "
                "two candidate columns. "
                f"Expected: {sorted(expected_columns)}. "
                f"Received: {sorted(columns)}."
            ),
            None,
        )

    # ============================================================
    # Candidate-specific semantic guardrails
    # ============================================================

    # ------------------------------------------------------------
    # column_comparison
    # ------------------------------------------------------------

    if rule_type == "column_comparison":

        left_column = parameters[
            "left_column"
        ]

        right_column = parameters[
            "right_column"
        ]

        operator = parameters[
            "operator"
        ]

        if left_column == right_column:
            return (
                False,
                "A column cannot be compared with itself.",
                None,
            )

        left_profile = profile_map.get(
            left_column
        )

        right_profile = profile_map.get(
            right_column
        )

        if (
            left_profile is None
            or right_profile is None
        ):
            return (
                False,
                (
                    "Unable to locate column profile "
                    "for comparison rule."
                ),
                None,
            )

        left_type = normalize_type(
            left_profile[
                "inferred_type"
            ]
        )

        right_type = normalize_type(
            right_profile[
                "inferred_type"
            ]
        )

        # The compared fields should represent compatible types.

        if left_type != right_type:
            return (
                False,
                (
                    "Column comparison uses incompatible "
                    f"types: {left_type} vs {right_type}."
                ),
                None,
            )

        # Ordered comparisons only make sense for date/numeric data.

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
                    "Ordered column comparisons require "
                    "numeric or date-compatible columns."
                ),
                None,
            )

        # --------------------------------------------------------
        # Empirical relationship enforcement
        #
        # If Python already established an observed date ordering,
        # the LLM cannot reverse or change it.
        # --------------------------------------------------------

        empirical_evidence = candidate.get(
            "empirical_evidence"
        )

        if (
            empirical_evidence
            and candidate.get(
                "candidate_type"
            )
            == "date_relationship"
        ):

            expected_left = (
                empirical_evidence[
                    "left_column"
                ]
            )

            expected_operator = (
                empirical_evidence[
                    "operator"
                ]
            )

            expected_right = (
                empirical_evidence[
                    "right_column"
                ]
            )

            if (
                left_column != expected_left
                or operator != expected_operator
                or right_column != expected_right
            ):
                return (
                    False,
                    (
                        "LLM comparison does not match "
                        "the empirically observed "
                        "relationship. "
                        f"Expected: "
                        f"{expected_left} "
                        f"{expected_operator} "
                        f"{expected_right}."
                    ),
                    None,
                )

    # ------------------------------------------------------------
    # conditional_required
    # ------------------------------------------------------------

    elif rule_type == "conditional_required":

        condition_column = parameters[
            "condition_column"
        ]

        required_column = parameters[
            "required_column"
        ]

        if condition_column == required_column:
            return (
                False,
                (
                    "conditional_required cannot use "
                    "the same column as both condition "
                    "and required field."
                ),
                None,
            )

        empirical = candidate.get("empirical_evidence") or {}
        expected = {
            "condition_column": empirical.get("condition_column"),
            "condition_operator": empirical.get("condition_operator"),
            "condition_value": empirical.get("condition_value"),
            "required_column": empirical.get("required_column"),
        }
        received = {key: parameters.get(key) for key in expected}
        if all(value is not None for value in expected.values()) and received != expected:
            return False, "LLM conditional rule does not match empirical evidence.", None

    # ------------------------------------------------------------
    # at_least_one_present
    # ------------------------------------------------------------

    elif rule_type == "at_least_one_present":

        rule_columns = parameters[
            "columns"
        ]

        if len(
            set(rule_columns)
        ) < 2:
            return (
                False,
                (
                    "at_least_one_present requires "
                    "at least two distinct columns."
                ),
                None,
            )

    # ------------------------------------------------------------
    # columns_equal
    # ------------------------------------------------------------

    elif rule_type == "columns_equal":

        rule_columns = parameters[
            "columns"
        ]

        if len(
            set(rule_columns)
        ) < 2:
            return (
                False,
                (
                    "columns_equal requires at least "
                    "two distinct columns."
                ),
                None,
            )

        # Critical semantic guardrail:
        #
        # columns_equal is ONLY appropriate when Python identified
        # the candidate as representing genuinely duplicate semantic
        # fields.
        #
        # This prevents:
        #
        # address == city
        # city == state
        # state == zip
        #
        # even if an LLM proposes them.

        if (
            candidate.get(
                "candidate_type"
            )
            != "duplicate_semantic_field"
        ):
            return (
                False,
                (
                    "columns_equal is only permitted "
                    "for duplicate semantic field "
                    "candidates."
                ),
                None,
            )

    # ============================================================
    # Build clean rule definition
    # ============================================================

    cleaned_rule = {

        "business_definition":
            response.get(
                "business_definition",
                "",
            ),

        "confidence_score":
            confidence,

        "evidence":
            response.get(
                "evidence",
                "",
            ),

        # Never trust the LLM's target_columns field.
        #
        # Derive it ourselves from the validated executable rule.

        "target_columns":
            columns,

        "executable_rule":
            executable_rule,
    }

    # ------------------------------------------------------------
    # Preserve empirical evidence in the rule itself.
    #
    # This will be valuable later in Rule Catalog, audit history,
    # and the AI Steward Copilot.
    # ------------------------------------------------------------

    empirical_evidence = candidate.get(
        "empirical_evidence"
    )

    if empirical_evidence:

        cleaned_rule["model_confidence_score"] = confidence
        cleaned_rule["confidence_score"] = min(
            confidence,
            float(empirical_evidence.get("support", confidence)),
        )

        cleaned_rule[
            "empirical_evidence"
        ] = empirical_evidence

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
                ,stewardship_run_id
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
                ,:stewardship_run_id
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

            "stewardship_run_id":
                get_stewardship_run_id(),
        },
    )


# ============================================================
# Main
# ============================================================

def main(dataset_name):

    print(
        f"Analyzing multi-column relationships "
        f"for dataset: {dataset_name}"
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
            dataset_name,
        )

        if dataset is None:

            raise ValueError(
                f"Dataset not found or inactive: "
                f"{dataset_name}"
            )

        dataset = dict(
            dataset
        )

        profiles = get_column_profiles(
            conn,
            dataset_name,
        )

        if not profiles:

            raise ValueError(
                f"No column profiles exist for "
                f"{dataset_name}. "
                "Run profiling first."
            )

        existing_rules = get_existing_rules(
            conn,
            dataset_name,
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
        dataset_name
    )

    discovered_candidates = discover_candidates(profiles)
    analysis_columns = sorted({
        column
        for candidate in discovered_candidates
        for column in (candidate["left_column"], candidate["right_column"])
    })
    if not analysis_columns:
        print("No structural multi-column candidates were discovered.")
        return
    select_columns = ", ".join(quote_identifier(column) for column in analysis_columns)
    df = pd.read_sql(
        f'SELECT {select_columns} FROM {quote_identifier(raw_schema)}.{quote_identifier(raw_table)} '
        f'LIMIT {EMPIRICAL_SAMPLE_ROWS}',
        engine
    )

    print(
        f"Raw rows available for empirical analysis: "
        f"{len(df)}"
    )

    # --------------------------------------------------------
    # Discover candidates
    # --------------------------------------------------------

    candidates, screening = screen_candidates(
        df,
        discovered_candidates,
        LLM_PROVIDER,
    )

    print()
    print(
        f"Columns analyzed: "
        f"{len(profiles)}"
    )

    print(
        f"Relationship candidates discovered: {screening['discovered']}"
    )
    print(
        f"Candidates with sufficient empirical support: {screening['supported']}"
    )
    print(
        f"Candidates selected for {LLM_PROVIDER}: {screening['selected_for_llm']}"
    )

    if not candidates:

        print(
            "No empirically supported multi-column candidates were selected."
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

    reviews = review_candidates(
        dataset,
        candidates,
        profile_map,
        LLM_PROVIDER,
    )

    # --------------------------------------------------------
    # Analyze each candidate
    # --------------------------------------------------------

    with engine.begin() as conn:

        for number, (candidate, review) in enumerate(
            zip(candidates, reviews),
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
            response, llm_error = review
            if llm_error is not None:
                total_llm_errors += 1
                print(
                    f"LLM error: {llm_error}"
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
                dataset_name,
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
        f"Candidates discovered: {screening['discovered']}"
    )
    print(
        f"Insufficient-data candidates: {screening['insufficient_data']}"
    )
    print(
        f"Low-support candidates: {screening['low_support']}"
    )
    print(
        f"Capacity-limited candidates: {screening['capacity_skipped']}"
    )
    print(
        f"Candidates sent to LLM: {screening['selected_for_llm']}"
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
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset_name")
    main(parser.parse_args().dataset_name)
