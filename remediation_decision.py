from sqlalchemy import text


def change_remediation_status(
    conn,
    remediation_id: int,
    new_status: str,
    changed_by: str,
    decision_note: str | None = None,
):
    if new_status not in {"proposed", "approved", "rejected", "applied"}:
        raise ValueError(f"Unsupported remediation status: {new_status}")

    actor = str(changed_by or "").strip()
    if not actor:
        raise ValueError("A steward identity is required for this decision.")

    remediation = conn.execute(
        text("""
            SELECT id, status
            FROM dq.remediation_suggestion
            WHERE id = :id
            FOR UPDATE
        """),
        {"id": int(remediation_id)},
    ).mappings().first()
    if remediation is None:
        raise ValueError(f"Remediation {remediation_id} was not found.")

    decision_fields = ""
    if new_status in {"approved", "rejected"}:
        decision_fields = ", approved_by = :changed_by, approved_at = CURRENT_TIMESTAMP"

    conn.execute(
        text(f"""
            UPDATE dq.remediation_suggestion
            SET status = :status {decision_fields}
            WHERE id = :id
        """),
        {"status": new_status, "changed_by": actor, "id": int(remediation_id)},
    )
    conn.execute(
        text("""
            INSERT INTO dq.remediation_audit
            (
                remediation_id, previous_status, new_status,
                changed_by, decision_note
            )
            VALUES
            (
                :remediation_id, :previous_status, :new_status,
                :changed_by, :decision_note
            )
        """),
        {
            "remediation_id": int(remediation_id),
            "previous_status": remediation["status"],
            "new_status": new_status,
            "changed_by": actor,
            "decision_note": decision_note or None,
        },
    )
    return new_status
