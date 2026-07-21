"""Tests for model-facing result serialization."""

import json

from engine.serialization import json_safe


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
