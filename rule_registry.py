from dataclasses import dataclass, field
from typing import Any


# ============================================================
# Rule specification
# ============================================================

@dataclass(frozen=True)
class RuleSpec:
    """
    Contract for one executable data-quality rule.

    scope:
        COLUMN
        ROW
        DATASET

    executor:
        Name of the evaluator function in evaluate_rules.py.

    required_parameters:
        Parameters that must exist in executable_rule.parameters.

    optional_parameters:
        Parameters that may be supplied.

    parameter_types:
        Expected Python types for parameters.

    allowed_operators:
        Optional list of legal operators.

    llm_generatable:
        Whether an LLM is allowed to propose this rule.
    """

    rule_type: str
    scope: str
    executor: str | None

    description: str

    required_parameters: tuple[str, ...] = ()
    optional_parameters: tuple[str, ...] = ()

    parameter_types: dict[str, Any] = field(
        default_factory=dict
    )

    allowed_operators: tuple[str, ...] = ()

    llm_generatable: bool = True

PARAMETER_ALIASES = {
    "date_format": {
        "format_string": "format",
    },
}

# ============================================================
# Canonical rule registry
# ============================================================

RULE_REGISTRY = {

    # ========================================================
    # COLUMN RULES
    # ========================================================

    "allowed_values": RuleSpec(
        rule_type="allowed_values",
        scope="COLUMN",
        executor="evaluate_allowed_values",
        description=(
            "Value must belong to a defined set of allowed values."
        ),
        required_parameters=(
            "values",
        ),
        parameter_types={
            "values": list,
        },
    ),

    "not_null": RuleSpec(
        rule_type="not_null",
        scope="COLUMN",
        executor="evaluate_not_null",
        description=(
            "Column value must not be null or blank."
        ),
    ),

    "max_length": RuleSpec(
        rule_type="max_length",
        scope="COLUMN",
        executor="evaluate_max_length",
        description=(
            "Text value must not exceed a maximum length."
        ),
        required_parameters=(
            "max_length",
        ),
        parameter_types={
            "max_length": int,
        },
    ),

    "min_length": RuleSpec(
        rule_type="min_length",
        scope="COLUMN",
        executor="evaluate_min_length",
        description=(
            "Text value must meet a minimum length."
        ),
        required_parameters=(
            "min_length",
        ),
        parameter_types={
            "min_length": int,
        },
    ),

    "regex": RuleSpec(
        rule_type="regex",
        scope="COLUMN",
        executor="evaluate_regex",
        description=(
            "Text value must match a regular expression."
        ),
        required_parameters=(
            "pattern",
        ),
        parameter_types={
            "pattern": str,
        },
    ),

    "numeric_range": RuleSpec(
        rule_type="numeric_range",
        scope="COLUMN",
        executor="evaluate_numeric_range",
        description=(
            "Numeric value must fall within a defined range."
        ),
        required_parameters=(
            "min",
            "max",
        ),
        parameter_types={
            "min": (int, float),
            "max": (int, float),
        },
    ),

    "percentage_range": RuleSpec(
        rule_type="percentage_range",
        scope="COLUMN",
        executor="evaluate_percentage_range",
        description=(
            "Percentage value must fall within a defined range."
        ),
        required_parameters=(
            "min",
            "max",
        ),
        parameter_types={
            "min": (int, float),
            "max": (int, float),
        },
    ),

    "date_format": RuleSpec(
        rule_type="date_format",
        scope="COLUMN",
        executor="evaluate_date_format",
        description=(
            "Value must conform to the specified date format."
        ),
        required_parameters=(
            "format",
        ),
        parameter_types={
            "format": str,
        },
    ),

    "city_contains_state_or_zip": RuleSpec(
        rule_type="city_contains_state_or_zip",
        scope="COLUMN",
        executor="evaluate_city_contains_state_or_zip",
        description=(
            "Detect malformed city values containing state or ZIP data."
        ),
        optional_parameters=(
            "city_column",
        ),
        parameter_types={
            "city_column": str,
        },
    ),

    "state_field_contains_zip": RuleSpec(
        rule_type="state_field_contains_zip",
        scope="COLUMN",
        executor="evaluate_state_field_contains_zip",
        description=(
            "Detect malformed state values containing ZIP data."
        ),
        optional_parameters=(
            "state_column",
        ),
        parameter_types={
            "state_column": str,
        },
    ),

    # ========================================================
    # ROW / MULTI-COLUMN RULES
    # ========================================================

    "column_comparison": RuleSpec(
        rule_type="column_comparison",
        scope="ROW",
        executor="evaluate_column_comparison",
        description=(
            "Compare two columns using a deterministic operator."
        ),
        required_parameters=(
            "left_column",
            "operator",
            "right_column",
        ),
        optional_parameters=(
            "null_behavior",
        ),
        parameter_types={
            "left_column": str,
            "operator": str,
            "right_column": str,
            "null_behavior": str,
        },
        allowed_operators=(
            "==",
            "!=",
            "<",
            "<=",
            ">",
            ">=",
        ),
    ),

    "conditional_required": RuleSpec(
        rule_type="conditional_required",
        scope="ROW",
        executor="evaluate_conditional_required",
        description=(
            "Require one column when another column satisfies a condition."
        ),
        required_parameters=(
            "condition_column",
            "condition_operator",
            "condition_value",
            "required_column",
        ),
        parameter_types={
            "condition_column": str,
            "condition_operator": str,
            "required_column": str,
        },
        allowed_operators=(
            "==",
        ),
    ),

    "at_least_one_present": RuleSpec(
        rule_type="at_least_one_present",
        scope="ROW",
        executor="evaluate_at_least_one_present",
        description=(
            "At least one column from a defined group must contain a value."
        ),
        required_parameters=(
            "columns",
        ),
        parameter_types={
            "columns": list,
        },
    ),

    "columns_equal": RuleSpec(
        rule_type="columns_equal",
        scope="ROW",
        executor="evaluate_columns_equal",
        description=(
            "Two or more semantically equivalent columns must contain "
            "equal values."
        ),
        required_parameters=(
            "columns",
        ),
        optional_parameters=(
            "ignore_nulls",
        ),
        parameter_types={
            "columns": list,
            "ignore_nulls": bool,
        },
    ),

    # ========================================================
    # DATASET RULES
    # ========================================================

    "minimum_row_count": RuleSpec(
        rule_type="minimum_row_count",
        scope="DATASET",
        executor="evaluate_minimum_row_count",
        description=(
            "Dataset must contain at least a minimum number of rows."
        ),
        required_parameters=(
            "minimum_rows",
        ),
        parameter_types={
            "minimum_rows": int,
        },
        llm_generatable=False,
    ),

    "primary_key_unique": RuleSpec(
        rule_type="primary_key_unique",
        scope="DATASET",
        executor="evaluate_primary_key_unique",
        description=(
            "Registered primary key must uniquely identify records."
        ),
        required_parameters=(
            "column",
        ),
        parameter_types={
            "column": str,
        },
        llm_generatable=False,
    ),

    "column_combination_unique": RuleSpec(
        rule_type="column_combination_unique",
        scope="DATASET",
        executor="evaluate_column_combination_unique",
        description=(
            "A defined combination of columns must uniquely identify rows."
        ),
        required_parameters=(
            "columns",
        ),
        parameter_types={
            "columns": list,
        },
        llm_generatable=False,
    ),
}


# ============================================================
# Aliases
#
# These protect us from older rules already stored in dq.rule
# while allowing the application to use one canonical name.
# ============================================================

RULE_ALIASES = {
    "city_ends_with_state_or_zip":
        "city_contains_state_or_zip",
        
    "pattern":
        "regex",
}


# ============================================================
# Registry helpers
# ============================================================

def canonical_rule_type(
    rule_type: str,
) -> str:
    """
    Convert an old or alternate rule name to the canonical
    registry rule type.
    """

    return RULE_ALIASES.get(
        rule_type,
        rule_type,
    )


def get_rule_spec(
    rule_type: str,
) -> RuleSpec | None:
    """
    Return the registered specification for a rule type.
    """

    canonical = canonical_rule_type(
        rule_type
    )

    return RULE_REGISTRY.get(
        canonical
    )


def get_rule_types(
    scope: str | None = None,
    llm_only: bool = False,
) -> list[str]:
    """
    Return rule types available for a scope.
    """

    results = []

    for rule_type, spec in RULE_REGISTRY.items():

        if (
            scope is not None
            and spec.scope != scope
        ):
            continue

        if (
            llm_only
            and not spec.llm_generatable
        ):
            continue

        results.append(
            rule_type
        )

    return sorted(
        results
    )


# ============================================================
# Rule validation
# ============================================================

def validate_executable_rule(
    executable_rule: dict,
    expected_scope: str | None = None,
) -> tuple[bool, str | None, dict | None]:
    """
    Validate an executable_rule object against the registry.

    Returns:

        (True, None, cleaned_rule)

    or:

        (False, reason, None)
    """

    if not isinstance(
        executable_rule,
        dict,
    ):
        return (
            False,
            "executable_rule must be a JSON object.",
            None,
        )

    rule_type = executable_rule.get(
        "type"
    )

    if not rule_type:
        return (
            False,
            "executable_rule.type is required.",
            None,
        )

    canonical_type = canonical_rule_type(
        rule_type
    )

    spec = RULE_REGISTRY.get(
        canonical_type
    )

    if spec is None:
        return (
            False,
            f"Unsupported rule type: {rule_type}",
            None,
        )

    if (
        expected_scope is not None
        and spec.scope != expected_scope
    ):
        return (
            False,
            (
                f"Rule '{canonical_type}' belongs to "
                f"scope {spec.scope}, not {expected_scope}."
            ),
            None,
        )

    parameters = executable_rule.get(
        "parameters",
        {}
    )

    # --------------------------------------------------------
    # Legacy parameter aliases
    # --------------------------------------------------------

    parameter_aliases = PARAMETER_ALIASES.get(
        canonical_type,
        {},
    )

    if parameter_aliases:

        parameters = dict(
            parameters
        )

        for old_name, new_name in parameter_aliases.items():

            if (
                old_name in parameters
                and new_name not in parameters
            ):
                parameters[
                    new_name
                ] = parameters.pop(
                    old_name
                )
    if parameters is None:
        parameters = {}

    if not isinstance(
        parameters,
        dict,
    ):
        return (
            False,
            "executable_rule.parameters must be a JSON object.",
            None,
        )

    # --------------------------------------------------------
    # Required parameters
    # --------------------------------------------------------

    for parameter in spec.required_parameters:

        if parameter not in parameters:
            return (
                False,
                (
                    f"Rule '{canonical_type}' is missing "
                    f"required parameter '{parameter}'."
                ),
                None,
            )

    # --------------------------------------------------------
    # Unexpected parameters
    # --------------------------------------------------------

    allowed_parameters = set(
        spec.required_parameters
    ) | set(
        spec.optional_parameters
    )

    unexpected = (
        set(parameters.keys())
        - allowed_parameters
    )

    if unexpected:
        return (
            False,
            (
                f"Rule '{canonical_type}' contains unsupported "
                f"parameters: {sorted(unexpected)}"
            ),
            None,
        )

    # --------------------------------------------------------
    # Parameter types
    # --------------------------------------------------------

    for parameter, expected_type in spec.parameter_types.items():

        if parameter not in parameters:
            continue

        value = parameters[
            parameter
        ]

        # condition_value intentionally accepts any JSON scalar.
        if parameter == "condition_value":
            continue

        if not isinstance(
            value,
            expected_type,
        ):
            return (
                False,
                (
                    f"Parameter '{parameter}' for rule "
                    f"'{canonical_type}' has invalid type."
                ),
                None,
            )

    # --------------------------------------------------------
    # Operator validation
    # --------------------------------------------------------

    if spec.allowed_operators:

        operator = (
            parameters.get("operator")
            or parameters.get(
                "condition_operator"
            )
        )

        if operator not in spec.allowed_operators:
            return (
                False,
                (
                    f"Operator '{operator}' is not allowed for "
                    f"rule '{canonical_type}'. "
                    f"Allowed operators: "
                    f"{list(spec.allowed_operators)}"
                ),
                None,
            )

    cleaned = {
        "type": canonical_type,
        "parameters": parameters,
    }

    return (
        True,
        None,
        cleaned,
    )


# ============================================================
# Prompt generation
# ============================================================

def build_llm_rule_catalog(
    scope: str,
) -> str:
    """
    Build the supported-rule section of an LLM prompt directly
    from the registry.

    This prevents prompt/evaluator drift.
    """

    sections = []

    for rule_type in get_rule_types(
        scope=scope,
        llm_only=True,
    ):

        spec = RULE_REGISTRY[
            rule_type
        ]

        lines = [
            f"- {rule_type}",
            f"  purpose: {spec.description}",
        ]

        if spec.required_parameters:

            lines.append(
                "  required parameters: "
                + ", ".join(
                    spec.required_parameters
                )
            )

        if spec.optional_parameters:

            lines.append(
                "  optional parameters: "
                + ", ".join(
                    spec.optional_parameters
                )
            )

        if spec.allowed_operators:

            lines.append(
                "  allowed operators: "
                + ", ".join(
                    spec.allowed_operators
                )
            )

        sections.append(
            "\n".join(lines)
        )

    return "\n\n".join(
        sections
    )