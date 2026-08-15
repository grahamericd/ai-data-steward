from scripts.profile_dataset import infer_type_from_sample, profile_sql_column


class FakeResult:
    def __init__(self, row=None, values=None):
        self.row = row
        self.values = values or []

    def mappings(self):
        return self

    def one(self):
        return self.row

    def scalars(self):
        return self

    def all(self):
        return self.values


class FakeConnection:
    def __init__(self):
        self.calls = []

    def execute(self, statement, parameters=None):
        self.calls.append((str(statement), parameters))
        if len(self.calls) == 1:
            return FakeResult(
                row={
                    "null_count": 2,
                    "distinct_count": 3,
                    "min_value": "A",
                    "max_value": "Z",
                }
            )
        return FakeResult(values=["A", "B", "Z"])


def test_sql_column_profile_returns_aggregates_and_bounded_samples():
    connection = FakeConnection()
    profile = profile_sql_column(
        connection,
        "raw",
        "customers",
        "status",
        "text",
        row_count=10,
        sample_limit=3,
    )

    assert profile == {
        "row_count": 10,
        "null_count": 2,
        "null_percent": 20.0,
        "distinct_count": 3,
        "sample_values": ["A", "B", "Z"],
        "inferred_type": "text",
        "min_value": "A",
        "max_value": "Z",
    }
    assert "COUNT(DISTINCT" in connection.calls[0][0]
    assert connection.calls[1][1] == {"sample_scan_limit": 100}


def test_database_type_drives_numeric_inference_without_full_column():
    assert infer_type_from_sample(["1", "2"], "bigint") == "integer"
    assert infer_type_from_sample(["1.5", "2.5"], "numeric") == "numeric"


def test_text_semantics_use_only_the_bounded_sample():
    assert infer_type_from_sample(["10%", "25%"], "text") == "percentage"
    assert infer_type_from_sample(["$10", "$20"], "character varying") == "currency"
