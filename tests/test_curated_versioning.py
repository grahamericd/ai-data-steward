from pathlib import Path

import pytest

from scripts.apply_remediations import curated_physical_table_name


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_curated_physical_tables_are_version_specific():
    assert curated_physical_table_name("customers", 41) == "customers__v41"
    assert curated_physical_table_name("customers", 42) == "customers__v42"


def test_curated_physical_table_rejects_unsafe_identifiers():
    with pytest.raises(ValueError):
        curated_physical_table_name("customers; DROP TABLE raw.customers", 1)


def test_curated_migration_captures_version_and_change_lineage():
    migration = (PROJECT_ROOT / "sql" / "010_curated_versioning.sql").read_text()

    required_contracts = (
        "curated.remediation_run",
        "curated.dataset_version",
        "previous_version_id",
        "curated.row_lineage",
        "raw_load_run_id",
        "curated.change_history",
        "remediation_id",
        "rule_id",
        "result_id",
        "failed_record_id",
        "applied_in_remediation_run_id",
    )
    for contract in required_contracts:
        assert contract in migration


def test_remediation_application_does_not_destroy_curated_tables():
    implementation = (PROJECT_ROOT / "scripts" / "apply_remediations.py").read_text().upper()

    assert "DROP TABLE" not in implementation
    assert "CREATE OR REPLACE VIEW" in implementation
    assert "CURATED.CHANGE_HISTORY" in implementation
