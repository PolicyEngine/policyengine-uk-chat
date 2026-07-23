"""Tests for model-facing result serialization."""

import json
from dataclasses import dataclass

import numpy as np
import pandas as pd
import pytest

from engine.serialization import explore_tabular_data, json_safe


def test_json_safe_replaces_non_finite_numbers_with_null():
    result = json_safe(
        {
            "nan": float("nan"),
            "positive_infinity": float("inf"),
            "negative_infinity": float("-inf"),
            "finite": 1.25,
        }
    )

    assert result == {
        "nan": None,
        "positive_infinity": None,
        "negative_infinity": None,
        "finite": 1.25,
    }
    assert "NaN" not in json.dumps(result)
    assert "Infinity" not in json.dumps(result)


def test_json_safe_handles_numpy_collections_and_scalars():
    assert json_safe(np.array([1, 2])) == [1, 2]
    assert json_safe(np.int64(3)) == 3
    assert json_safe(np.float64(2.5)) == 2.5
    assert json_safe(np.float64(np.nan)) is None
    assert json_safe(np.bool_(True)) is True


def test_json_safe_rejects_tabular_simulation_data():
    with pytest.raises(TypeError, match="typed result fields"):
        json_safe(pd.DataFrame({"value": [1]}))
    with pytest.raises(TypeError, match="typed result fields"):
        json_safe(pd.Series([1]))


def test_json_safe_handles_nested_and_model_like_values():
    class ModernModel:
        def model_dump(self):
            return {1: (float("inf"), "ok")}

    class LegacyModel:
        def dict(self):
            return {"items": {1, 2}}

    @dataclass
    class Record:
        amount: float

    class Fallback:
        def __str__(self):
            return "fallback"

    assert json_safe(ModernModel()) == {"1": [None, "ok"]}
    assert sorted(json_safe(LegacyModel())["items"]) == [1, 2]
    assert json_safe(Record(amount=1.5)) == {"amount": 1.5}
    assert json_safe(Fallback()) == "fallback"
    assert json_safe(None) is None


def test_explore_tabular_data_rejects_invalid_inputs():
    expected = {
        "error": "Data must be a non-empty list of dicts",
        "row_count": 0,
        "columns": [],
    }
    assert explore_tabular_data([]) == expected
    assert explore_tabular_data(["not-a-row"]) == expected


def test_explore_tabular_data_describes_columns_and_numeric_ranges():
    result = explore_tabular_data(
        [
            {"amount": 2, "mixed": 1},
            {"amount": None, "mixed": "one"},
            {"amount": 5, "other": True},
        ]
    )

    assert result["row_count"] == 3
    columns = {column["name"]: column for column in result["columns"]}
    assert columns["amount"] == {
        "name": "amount",
        "type": "int",
        "unique_count": 2,
        "null_count": 1,
        "unique_values": [2, 5],
        "min": 2,
        "max": 5,
    }
    assert set(columns["mixed"]["unique_values"]) == {1, "one"}
    assert columns["other"]["null_count"] == 2


def test_explore_tabular_data_omits_large_unique_value_lists():
    result = explore_tabular_data(
        [{"value": value} for value in range(4)],
        max_unique_values=2,
    )

    assert "unique_values" not in result["columns"][0]
