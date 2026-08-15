from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from scripts.load_dataset import (
    load_dataset,
    parse_csv,
    parse_fixed_width_record,
    validate_primary_key,
)


FIXTURES = Path(__file__).parent / "fixtures"
CSV_FIXTURE = FIXTURES / "stewardship_customers.csv"


def test_small_integration_dataset_loads_as_strings():
    dataframe = parse_csv(CSV_FIXTURE)
    assert dataframe["customer_id"].tolist() == ["C001", "C002", "C003", "C004"]
    assert dataframe.loc[1, "city"] == "Miami, FL"
    assert dataframe.loc[0, "zip"] == "02108"


def test_fixed_width_record_uses_registered_positions():
    fields = [
        {"column_name": "customer_id", "start_position": 1, "field_length": 4},
        {"column_name": "name", "start_position": 5, "field_length": 10},
        {"column_name": "state", "start_position": 15, "field_length": 2},
    ]
    line = (FIXTURES / "stewardship_customers_fixed_width.txt").read_text().splitlines()[0]
    assert parse_fixed_width_record(line, fields) == {
        "customer_id": "C001",
        "name": "ALICE",
        "state": "NY",
    }


def test_duplicate_primary_keys_are_rejected():
    dataframe = pd.DataFrame({"customer_id": ["C001", "C001"]})
    with pytest.raises(ValueError, match="duplicate primary-key values"):
        validate_primary_key(dataframe, "customer_id")


@pytest.mark.parametrize(
    ("mode", "expected_function"),
    [
        ("replace", "replace_table"),
        ("append", "append_table"),
        ("upsert", "upsert_table"),
    ],
)
def test_existing_table_dispatches_each_incremental_load_mode(mode, expected_function):
    dataset = {
        "dataset_id": 1,
        "dataset_name": "stewardship_customers",
        "raw_schema": "raw",
        "raw_table": "stewardship_customers",
        "source_type": "csv",
        "source_file": CSV_FIXTURE.name,
        "load_mode": mode,
        "primary_key": "customer_id",
    }
    operation_result = {"rows_inserted": 4, "rows_updated": 0}

    with (
        patch("scripts.load_dataset.engine.begin") as begin,
        patch("scripts.load_dataset.get_dataset_config", return_value=dataset),
        patch("scripts.load_dataset.resolve_source_file", return_value=CSV_FIXTURE),
        patch("scripts.load_dataset.create_load_run", return_value=99),
        patch("scripts.load_dataset.table_exists", return_value=True),
        patch("scripts.load_dataset.validate_columns_match"),
        patch("scripts.load_dataset.complete_load_run"),
        patch("scripts.load_dataset.fail_load_run"),
        patch("scripts.load_dataset.replace_table", return_value=operation_result) as replace,
        patch("scripts.load_dataset.append_table", return_value=operation_result) as append,
        patch("scripts.load_dataset.upsert_table", return_value=operation_result) as upsert,
    ):
        begin.return_value.__enter__.return_value = MagicMock()
        summary = load_dataset(
            "stewardship_customers",
            supplied_file=str(CSV_FIXTURE),
            requested_mode=mode,
        )

    operations = {"replace_table": replace, "append_table": append, "upsert_table": upsert}
    operations[expected_function].assert_called_once()
    assert summary["load_mode"] == mode
    assert summary["load_run_id"] == 99
