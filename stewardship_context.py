import os


def get_stewardship_run_id() -> int | None:
    """Return the orchestration run propagated to the current process."""

    value = os.getenv("AI_STEWARD_RUN_ID", "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError("AI_STEWARD_RUN_ID must be an integer.") from exc


def get_stewardship_actor() -> str | None:
    return os.getenv("AI_STEWARD_ACTOR", "").strip() or None
