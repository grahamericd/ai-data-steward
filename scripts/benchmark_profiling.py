import argparse
import sys
import time
import tracemalloc
from pathlib import Path

import pandas as pd
from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import engine
from scripts.load_dataset import quote_identifier, validate_identifier
from scripts.profile_dataset import collect_sql_profiles, get_dataset_config


def measure(function):
    tracemalloc.start()
    started = time.perf_counter()
    result = function()
    elapsed = time.perf_counter() - started
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return result, elapsed, peak_bytes / (1024 * 1024)


def benchmark(dataset_name):
    with engine.begin() as conn:
        dataset = get_dataset_config(conn, dataset_name)
        if dataset is None:
            raise ValueError(f"Dataset not found: {dataset_name}")
        schema = validate_identifier(dataset["raw_schema"] or "raw", "source schema")
        table = validate_identifier(dataset["raw_table"], "source table")

    qualified = f"{quote_identifier(schema)}.{quote_identifier(table)}"

    def legacy_full_table_read():
        dataframe = pd.read_sql(text(f"SELECT * FROM {qualified}"), engine)
        for column in dataframe.columns:
            series = dataframe[column]
            _ = {
                "row_count": len(series),
                "null_count": series.isna().sum(),
                "distinct_count": series.nunique(dropna=True),
                "min": series.dropna().astype(str).min() if series.notna().any() else None,
                "max": series.dropna().astype(str).max() if series.notna().any() else None,
                "sample": series.dropna().astype(str).drop_duplicates().head(10).tolist(),
            }
        return {"rows": len(dataframe), "columns": len(dataframe.columns)}

    def sql_native_profile():
        with engine.begin() as conn:
            profiles = collect_sql_profiles(conn, dataset_name)
        return {"columns": len(profiles)}

    legacy_result, legacy_seconds, legacy_peak = measure(legacy_full_table_read)
    sql_result, sql_seconds, sql_peak = measure(sql_native_profile)

    print(f"Dataset: {dataset_name}")
    print(f"Legacy pandas: {legacy_seconds:.3f}s, peak Python memory {legacy_peak:.2f} MiB, {legacy_result}")
    print(f"SQL native:    {sql_seconds:.3f}s, peak Python memory {sql_peak:.2f} MiB, {sql_result}")
    if sql_peak:
        print(f"Python-memory reduction: {legacy_peak / sql_peak:.2f}x")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset_name")
    args = parser.parse_args()
    benchmark(args.dataset_name)


if __name__ == "__main__":
    main()
