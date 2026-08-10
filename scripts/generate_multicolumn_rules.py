import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from sqlalchemy import text


# ---------------------------------------------------------------------
# Project imports
# ---------------------------------------------------------------------

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


# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------

MAX_GROUP_SIZE = 10
MAX_RULES_PER_GROUP = 3
MIN_CONFIDENCE = 0.70

SUPPORTED_RULE_TYPES = {
    "column_comparison",
    "conditional_required",
    "at_least_one_present",
    "columns_equal",
}

SUPPORTED_COMPARISON_OPERATORS = {
    "==",
    "!=",
    "<",
    "<=",
    ">",
    ">=",
}


# ---------------------------------------------------------------------
# Command-line argument
# ---------------------------------------------------------------------

if len(sys.argv) != 2:
    print(
        "Usage: python generate_multicolumn_rules.py <dataset_name>"
    )
    sys.exit(1)

DATASET_NAME = sys.argv[1]


# ---------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------

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
        {"dataset_name": dataset_name},
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
              AND status NOT IN ('rejected', 'retired')
            ORDER BY id
        """),
        {"dataset_name": dataset_name},
    ).mappings().all()


# ---------------------------------------------------------------------
# Profile helpers
# ---------------------------------------------------------------------

def profile_to_dict(profile):
    return {
        "column_name": profile["column_name"],
        "inferred_type": profile["inferred_type"],
        "null_percent": profile["null_percent"],
        "distinct_count": profile["distinct_count"],
        "sample_values": profile["sample_values"],
    }


def normalize_name(name):
    return re.sub(
        r"[^a-z0-9]+",
        "_",
        name.lower(),
    ).strip("_")


def tokens_for_column(name):
    tokens = [
        token
        for token in normalize_name(name).split("_")
        if token
    ]

    stop_words = {
        "the",
        "of",
        "a",
        "an",
        "field",
        "value",
        "code",
        "number",
        "num",
        "seq",
        "sequence",
        "id",
    }

    return [
        token
        for token in tokens
        if token not in stop_words
    ]


# ---------------------------------------------------------------------
# Candidate discovery
# ---------------------------------------------------------------------

def discover_candidate_groups(profiles):
    """
    Identify likely multi-column relationships before asking the LLM.

    Returns a list of dictionaries:
    {
        "reason": "...",
        "columns": [...]
    }
    """

    profile_map = {
        profile["column_name"]: profile
        for profile in profiles
    }

    column_names = list(profile_map.keys())

    groups = []
    seen = set()

    def add_group(reason, columns):
        cleaned = []

        for column in columns:
            if (
                column in profile_map
                and column not in cleaned
            ):
                cleaned.append(column)

        if len(cleaned) < 2:
            return

        if len(cleaned) > MAX_GROUP_SIZE:
            cleaned = cleaned[:MAX_GROUP_SIZE]

        key = tuple(sorted(cleaned))

        if key in seen:
            return

        seen.add(key)

        groups.append(
            {
                "reason": reason,
                "columns": cleaned,
            }
        )

    # -------------------------------------------------------------
    # 1. Date groups
    # -------------------------------------------------------------

    date_columns = []

    for profile in profiles:
        name = profile["column_name"].lower()
        inferred = str(
            profile["inferred_type"] or ""
        ).lower()

        if (
            "date" in name
            or "time" in name
            or inferred in {
                "date",
                "datetime",
                "timestamp",
            }
        ):
            date_columns.append(
                profile["column_name"]
            )

    # Avoid throwing every date field into one giant prompt.
    for i in range(0, len(date_columns), MAX_GROUP_SIZE):
        chunk = date_columns[
            i:i + MAX_GROUP_SIZE
        ]

        add_group(
            "Columns appear to represent dates or timestamps.",
            chunk,
        )

    # -------------------------------------------------------------
    # 2. Status + date groups
    # -------------------------------------------------------------

    status_columns = [
        profile["column_name"]
        for profile in profiles
        if any(
            token in profile["column_name"].lower()
            for token in [
                "status",
                "state",
                "active",
                "inactive",
                "cancel",
                "expire",
                "dissol",
                "event",
            ]
        )
    ]

    for status_column in status_columns:
        related = [
            status_column
        ]

        status_tokens = set(
            tokens_for_column(
                status_column
            )
        )

        for date_column in date_columns:
            date_tokens = set(
                tokens_for_column(
                    date_column
                )
            )

            if (
                status_tokens
                & date_tokens
            ):
                related.append(
                    date_column
                )

        # Even without shared tokens, a status field plus a few date
        # fields can be a useful candidate group.
        if len(related) == 1:
            related.extend(
                date_columns[:4]
            )

        add_group(
            "Status or lifecycle fields may have conditional relationships "
            "with date fields.",
            related,
        )

    # -------------------------------------------------------------
    # 3. Address/location groups
    # -------------------------------------------------------------

    location_keywords = {
        "address",
        "addr",
        "city",
        "state",
        "zip",
        "postal",
        "county",
        "country",
    }

    location_columns = [
        profile["column_name"]
        for profile in profiles
        if any(
            keyword in profile["column_name"].lower()
            for keyword in location_keywords
        )
    ]

    # Group location fields by shared prefix.
    prefix_groups = defaultdict(list)

    for column in location_columns:
        normalized = normalize_name(
            column
        )

        parts = normalized.split("_")

        if len(parts) > 1:
            prefix = "_".join(
                parts[:-1]
            )
        else:
            prefix = normalized

        prefix_groups[
            prefix
        ].append(column)

    for prefix, columns in prefix_groups.items():
        if len(columns) >= 2:
            add_group(
                f"Location fields share a common prefix: {prefix}",
                columns,
            )

    # -------------------------------------------------------------
    # 4. Shared semantic tokens
    # -------------------------------------------------------------

    token_groups = defaultdict(list)

    for column in column_names:
        for token in tokens_for_column(
            column
        ):
            if len(token) >= 4:
                token_groups[
                    token
                ].append(column)

    for token, columns in token_groups.items():
        if 2 <= len(columns) <= MAX_GROUP_SIZE:
            add_group(
                f"Columns share semantic token '{token}'.",
                columns,
            )

    # -------------------------------------------------------------
    # 5. Principal / repeating entity groups
    # -------------------------------------------------------------

    repeating_patterns = defaultdict(list)

    for column in column_names:
        normalized = normalize_name(
            column
        )

        # Convert:
        # principal_1_name
        # principal_2_name
        # into:
        # principal_{n}_name

        generalized = re.sub(
            r"(^|_)\d+(_|$)",
            r"\1{n}\2",
            normalized,
        )

        repeating_patterns[
            generalized
        ].append(column)

    for pattern, columns in repeating_patterns.items():
        if len(columns) >= 2:
            add_group(
                f"Columns appear to be repeated instances of '{pattern}'.",
                columns,
            )

    # -------------------------------------------------------------
    # 6. Identifier groups
    # -------------------------------------------------------------

    id_keywords = {
        "id",
        "number",
        "doc",
        "document",
        "seq",
        "sequence",
        "key",
        "code",
    }

    identifier_columns = [
        column
        for column in column_names
        if any(
            keyword in column.lower()
            for keyword in id_keywords
        )
    ]

    # Break identifiers into reasonable chunks.
    for i in range(
        0,
        len(identifier_columns),
        MAX_GROUP_SIZE,
    ):
        chunk = identifier_columns[
            i:i + MAX_GROUP_SIZE
        ]

        add_group(
            "Columns appear to contain related identifiers or sequence values.",
            chunk,
        )

    return groups


# ---------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------

def build_group_prompt(
    dataset,
    group,
    profiles,
    existing_rules,
):
    profile_map = {
        profile["column_name"]: profile
        for profile in profiles
    }

    selected_profiles = [
        profile_to_dict(
            profile_map[column]
        )
        for column in group["columns"]
    ]

    relevant_existing_rules = []

    group_columns = set(
        group["columns"]
    )

    for rule in existing_rules:
        targets = rule[
            "target_columns"
        ]

        if isinstance(
            targets,
            str,
        ):
            try:
                targets = json.loads(
                    targets
                )
            except json.JSONDecodeError:
                targets = []

        targets = set(
            targets or []
        )

        if targets & group_columns:
            relevant_existing_rules.append(
                {
                    "id": rule["id"],
                    "scope": rule["rule_scope"],
                    "target_columns": list(
                        targets
                    ),
                    "status": rule["status"],
                    "rule_definition": rule[
                        "rule_definition"
                    ],
                }
            )

    return f"""
You are a senior enterprise data steward.

Analyze ONLY the following small group of related columns and determine
whether there are meaningful deterministic relationships between them.

Dataset:
{dataset['dataset_name']}

Dataset description:
{dataset.get('description')}

Candidate-group reason:
{group['reason']}

Column profiles:
{json.dumps(selected_profiles, indent=2, default=str)}

Relevant existing rules:
{json.dumps(relevant_existing_rules, indent=2, default=str)}

Your job is to propose up to {MAX_RULES_PER_GROUP} high-confidence
ROW-level data quality rules.

Do NOT propose single-column rules.

Do NOT invent business requirements merely because columns appear related.

If evidence is insufficient, return an empty rules array.

SUPPORTED RULE TYPES:

1. column_comparison

{{
  "type": "column_comparison",
  "parameters": {{
    "left_column": "column_a",
    "operator": "<=",
    "right_column": "column_b",
    "null_behavior": "ignore"
  }}
}}

Allowed operators:
==
!=
<
<=
>
>=

2. conditional_required

{{
  "type": "conditional_required",
  "parameters": {{
    "condition_column": "status",
    "condition_operator": "==",
    "condition_value": "C",
    "required_column": "cancel_date"
  }}
}}

condition_operator must be ==

3. at_least_one_present

{{
  "type": "at_least_one_present",
  "parameters": {{
    "columns": [
      "field_a",
      "field_b"
    ]
  }}
}}

4. columns_equal

{{
  "type": "columns_equal",
  "parameters": {{
    "columns": [
      "field_a",
      "field_b"
    ],
    "ignore_nulls": true
  }}
}}

Return ONLY:

{{
  "rules": [
    {{
      "business_definition": "",
      "confidence_score": 0.0,
      "evidence": "",
      "target_columns": [],
      "executable_rule": {{
        "type": "",
        "parameters": {{}}
      }}
    }}
  ]
}}

Requirements:

- Return only valid JSON.
- No Markdown.
- No SQL.
- Maximum {MAX_RULES_PER_GROUP} rules.
- Every rule must involve at least two distinct supplied columns.
- Never reference a column outside this group.
- confidence_score must be between 0 and 1.
"""


# ---------------------------------------------------------------------
# Rule interpretation
# ---------------------------------------------------------------------

def get_referenced_columns(rule):
    executable_rule = rule.get(
        "executable_rule",
        {},
    )

    rule_type = executable_rule.get(
        "type",
        "",
    )

    parameters = executable_rule.get(
        "parameters",
        {},
    )

    columns = []

    if rule_type == "column_comparison":
        columns.extend(
            [
                parameters.get(
                    "left_column"
                ),
                parameters.get(
                    "right_column"
                ),
            ]
        )

    elif rule_type == "conditional_required":
        columns.extend(
            [
                parameters.get(
                    "condition_column"
                ),
                parameters.get(
                    "required_column"
                ),
            ]
        )

    elif rule_type in {
        "at_least_one_present",
        "columns_equal",
    }:
        columns.extend(
            parameters.get(
                "columns",
                [],
            )
        )

    cleaned = []

    for column in columns:
        if (
            column
            and column not in cleaned
        ):
            cleaned.append(column)

    return cleaned


# ---------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------

def validate_rule(
    rule,
    valid_columns,
):
    if not isinstance(
        rule,
        dict,
    ):
        return (
            False,
            "Rule is not an object.",
            rule,
        )

    executable_rule = rule.get(
        "executable_rule"
    )

    if not isinstance(
        executable_rule,
        dict,
    ):
        return (
            False,
            "Missing executable_rule.",
            rule,
        )

    rule_type = executable_rule.get(
        "type"
    )

    if rule_type not in SUPPORTED_RULE_TYPES:
        return (
            False,
            f"Unsupported rule type: {rule_type}",
            rule,
        )

    parameters = executable_rule.get(
        "parameters",
        {},
    )

    if not isinstance(
        parameters,
        dict,
    ):
        return (
            False,
            "parameters must be an object.",
            rule,
        )

    referenced_columns = get_referenced_columns(
        rule
    )

    if len(
        set(referenced_columns)
    ) < 2:
        return (
            False,
            "Rule must reference at least two distinct columns.",
            rule,
        )

    bad_columns = [
        column
        for column in referenced_columns
        if column not in valid_columns
    ]

    if bad_columns:
        return (
            False,
            f"Unknown columns: {bad_columns}",
            rule,
        )

    if rule_type == "column_comparison":
        left = parameters.get(
            "left_column"
        )

        right = parameters.get(
            "right_column"
        )

        operator = parameters.get(
            "operator"
        )

        if left == right:
            return (
                False,
                "A column cannot be compared to itself.",
                rule,
            )

        if operator not in SUPPORTED_COMPARISON_OPERATORS:
            return (
                False,
                f"Unsupported operator: {operator}",
                rule,
            )

        null_behavior = parameters.get(
            "null_behavior",
            "ignore",
        )

        if null_behavior not in {
            "ignore",
            "fail",
        }:
            return (
                False,
                f"Unsupported null behavior: {null_behavior}",
                rule,
            )

    elif rule_type == "conditional_required":
        if parameters.get(
            "condition_operator"
        ) != "==":
            return (
                False,
                "conditional_required supports only ==.",
                rule,
            )

        if "condition_value" not in parameters:
            return (
                False,
                "condition_value is required.",
                rule,
            )

    elif rule_type in {
        "at_least_one_present",
        "columns_equal",
    }:
        columns = parameters.get(
            "columns"
        )

        if not isinstance(
            columns,
            list,
        ):
            return (
                False,
                "columns must be a list.",
                rule,
            )

        if len(
            set(columns)
        ) < 2:
            return (
                False,
                "Rule needs at least two distinct columns.",
                rule,
            )

    try:
        confidence = float(
            rule.get(
                "confidence_score",
                0,
            )
        )

    except (
        ValueError,
        TypeError,
    ):
        return (
            False,
            "confidence_score must be numeric.",
            rule,
        )

    if confidence < MIN_CONFIDENCE:
        return (
            False,
            f"Confidence too low: {confidence}",
            rule,
        )

    if confidence > 1:
        return (
            False,
            "confidence_score cannot exceed 1.",
            rule,
        )

    rule[
        "target_columns"
    ] = referenced_columns

    return (
        True,
        None,
        rule,
    )


# ---------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------

def executable_signature(rule):
    return json.dumps(
        rule.get(
            "executable_rule",
            {},
        ),
        sort_keys=True,
        default=str,
    )


def get_existing_signatures(existing_rules):
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

        signatures.add(
            executable_signature(
                definition
            )
        )

    return signatures


# ---------------------------------------------------------------------
# Insert
# ---------------------------------------------------------------------

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
            "dataset_name": dataset_name,
            "rule_definition": json.dumps(
                rule
            ),
            "target_columns": json.dumps(
                rule["target_columns"]
            ),
            "llm_provider": LLM_PROVIDER,
            "llm_model": LLM_MODEL,
            "prompt_version": (
                "multicolumn-candidate-v2"
            ),
        },
    )


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main():
    print(
        f"Analyzing multi-column relationships for "
        f"dataset: {DATASET_NAME}"
    )

    print(
        f"LLM provider: {LLM_PROVIDER}"
    )

    print(
        f"LLM model: {LLM_MODEL}"
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

        profiles = get_column_profiles(
            conn,
            DATASET_NAME,
        )

        if not profiles:
            raise ValueError(
                f"No profiles exist for "
                f"{DATASET_NAME}. "
                "Run profiling first."
            )

        existing_rules = get_existing_rules(
            conn,
            DATASET_NAME,
        )

    groups = discover_candidate_groups(
        profiles
    )

    print(
        f"Columns available: {len(profiles)}"
    )

    print(
        f"Candidate groups discovered: {len(groups)}"
    )

    print(
        f"Existing active rules: {len(existing_rules)}"
    )

    if not groups:
        print(
            "No candidate multi-column groups were discovered."
        )
        return

    existing_signatures = get_existing_signatures(
        existing_rules
    )

    inserted_signatures = set()

    total_proposed = 0
    total_inserted = 0
    total_rejected = 0
    total_duplicates = 0
    total_failed_groups = 0

    with engine.begin() as conn:

        for group_number, group in enumerate(
            groups,
            start=1,
        ):
            print()
            print(
                f"Group {group_number}/{len(groups)}"
            )

            print(
                f"Reason: {group['reason']}"
            )

            print(
                "Columns: "
                + ", ".join(
                    group["columns"]
                )
            )

            prompt = build_group_prompt(
                dataset,
                group,
                profiles,
                existing_rules,
            )

            try:
                response = generate_json(
                    prompt
                )

            except LLMError as exc:
                total_failed_groups += 1

                print(
                    f"Group skipped: LLM error: {exc}"
                )

                continue

            rules = response.get(
                "rules",
                []
            )

            if not isinstance(
                rules,
                list,
            ):
                total_failed_groups += 1

                print(
                    "Group skipped: response did not contain a rules list."
                )

                continue

            rules = rules[
                :MAX_RULES_PER_GROUP
            ]

            if not rules:
                print(
                    "No relationships proposed."
                )
                continue

            group_columns = set(
                group["columns"]
            )

            for rule in rules:
                total_proposed += 1

                valid, reason, cleaned = validate_rule(
                    rule,
                    group_columns,
                )

                if not valid:
                    total_rejected += 1

                    print(
                        f"Rejected: {reason}"
                    )

                    continue

                signature = executable_signature(
                    cleaned
                )

                if (
                    signature in existing_signatures
                    or signature in inserted_signatures
                ):
                    total_duplicates += 1

                    print(
                        "Duplicate skipped."
                    )

                    continue

                insert_rule(
                    conn,
                    DATASET_NAME,
                    cleaned,
                )

                inserted_signatures.add(
                    signature
                )

                total_inserted += 1

                print(
                    "Inserted: "
                    f"{cleaned['executable_rule']['type']} "
                    f"{cleaned['target_columns']}"
                )

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
        f"Candidate groups: {len(groups)}"
    )

    print(
        f"Groups with LLM errors: {total_failed_groups}"
    )

    print(
        f"Rules proposed: {total_proposed}"
    )

    print(
        f"Rules inserted: {total_inserted}"
    )

    print(
        f"Guardrail rejections: {total_rejected}"
    )

    print(
        f"Duplicates skipped: {total_duplicates}"
    )


if __name__ == "__main__":
    main()