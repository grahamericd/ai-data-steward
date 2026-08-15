import json

from sqlalchemy import text

from rule_registry import validate_rule_for_approval


def _require_actor(changed_by: str) -> str:
    actor = str(changed_by or "").strip()
    if not actor:
        raise ValueError("A steward identity is required for this decision.")
    return actor


def approve_rule(
    conn,
    rule_id: int,
    changed_by: str,
    decision_note: str | None = None,
) -> str:
    """Approve a rule only after registry validation and canonicalization."""

    rule = conn.execute(
        text("""
            SELECT id, status, rule_scope, rule_definition
            FROM dq.rule
            WHERE id = :id
            FOR UPDATE
        """),
        {"id": int(rule_id)},
    ).mappings().first()

    if rule is None:
        raise ValueError(f"Rule {rule_id} was not found.")

    actor = _require_actor(changed_by)
    valid, reason, normalized = validate_rule_for_approval(
        rule["rule_definition"],
        rule["rule_scope"] or "COLUMN",
    )
    if not valid:
        raise ValueError(f"Rule {rule_id} cannot be approved: {reason}")

    canonical_type = normalized["executable_rule"]["type"]
    conn.execute(
        text("""
            UPDATE dq.rule
            SET status = 'approved',
                rule_type = :rule_type,
                rule_definition = CAST(:rule_definition AS jsonb),
                decision_by = :decision_by,
                decision_at = CURRENT_TIMESTAMP
            WHERE id = :id
        """),
        {
            "id": int(rule_id),
            "rule_type": canonical_type,
            "rule_definition": json.dumps(normalized),
            "decision_by": actor,
        },
    )
    _write_rule_audit(
        conn, rule_id, rule["status"], "approved", actor, decision_note
    )
    return canonical_type


def _write_rule_audit(
    conn, rule_id, previous_status, new_status, changed_by, decision_note=None
):
    conn.execute(
        text("""
            INSERT INTO dq.rule_audit
            (rule_id, previous_status, new_status, changed_by, decision_note)
            VALUES
            (:rule_id, :previous_status, :new_status, :changed_by, :decision_note)
        """),
        {
            "rule_id": int(rule_id),
            "previous_status": previous_status,
            "new_status": new_status,
            "changed_by": changed_by,
            "decision_note": decision_note or None,
        },
    )


def change_rule_status(
    conn,
    rule_id: int,
    new_status: str,
    changed_by: str,
    decision_note: str | None = None,
):
    if new_status == "approved":
        return approve_rule(conn, rule_id, changed_by, decision_note)

    if new_status not in {"proposed", "rejected", "retired"}:
        raise ValueError(f"Unsupported rule status: {new_status}")

    actor = _require_actor(changed_by)
    rule = conn.execute(
        text("SELECT id, status FROM dq.rule WHERE id = :id FOR UPDATE"),
        {"id": int(rule_id)},
    ).mappings().first()
    if rule is None:
        raise ValueError(f"Rule {rule_id} was not found.")

    conn.execute(
        text("""
            UPDATE dq.rule
            SET status = :status,
                decision_by = :decision_by,
                decision_at = CURRENT_TIMESTAMP
            WHERE id = :id
        """),
        {"status": new_status, "decision_by": actor, "id": int(rule_id)},
    )
    _write_rule_audit(
        conn, rule_id, rule["status"], new_status, actor, decision_note
    )
    return new_status
