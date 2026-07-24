"""Tests for the synthetic-household axes engine boundary."""

import json
from types import SimpleNamespace

import pytest

from conftest import requires_policyengine_py
from engine import axes


def _model():
    return SimpleNamespace(
        variables_by_name={
            "employment_income": SimpleNamespace(
                value_type=float,
                entity="person",
            ),
            "income_tax": SimpleNamespace(
                value_type=float,
                entity="person",
            ),
            "household_net_income": SimpleNamespace(
                value_type=float,
                entity="household",
            ),
            "region": SimpleNamespace(
                value_type=str,
                entity="household",
            ),
        }
    )


def _validation(*, reform=None, **_kwargs):
    return {
        "valid": True,
        "year": 2026,
        "normalized_reform": reform or {},
    }


def _calculator(**kwargs):
    reform_offset = 100 if kwargs.get("reform") else 0
    return {
        "person": [
            {
                "employment_income": [10, 10, 10],
                "income_tax": [1 + reform_offset, 2 + reform_offset, 3 + reform_offset],
            },
            {
                "employment_income": [0, 50_000, 100_000],
                "income_tax": [
                    4 + reform_offset,
                    5 + reform_offset,
                    6 + reform_offset,
                ],
            },
        ],
        "benunit": {},
        "household": {
            "household_net_income": [
                20_000 + reform_offset,
                50_000 + reform_offset,
                80_000 + reform_offset,
            ]
        },
    }


def _patch_runtime(monkeypatch):
    monkeypatch.setattr(axes, "uk_model_version", _model)
    monkeypatch.setattr(axes, "validate_household_dict", _validation)
    monkeypatch.setattr(axes, "calculate_household_py", _calculator)


def test_build_axes_simulation_keeps_only_selected_series(monkeypatch):
    _patch_runtime(monkeypatch)

    run = axes.build_axes_simulation(
        people=[{"age": 30}, {"age": 31}],
        benunit=None,
        household=None,
        year=2026,
        reform=None,
        axis={
            "name": "employment_income",
            "index": 1,
            "min": 0,
            "max": 100_000,
            "count": 3,
        },
        outputs=["household_net_income", "income_tax"],
    )

    assert run.metadata() == {
        "status": "success",
        "year": 2026,
        "axis": {
            "name": "employment_income",
            "index": 1,
            "min": 0,
            "max": 100_000,
            "count": 3,
        },
        "outputs": [
            {
                "name": "household_net_income",
                "entity": "household",
                "entity_count": 1,
            },
            {"name": "income_tax", "entity": "person", "entity_count": 2},
        ],
        "targets": ["baseline"],
        "point_count": 3,
    }
    assert run.get_series(variable="income_tax", index=1) == {
        "household_input": {
            "people": [{"age": 30}, {"age": 31}],
            "benunit": {},
            "household": {},
            "year": 2026,
        },
        "axis": {"name": "employment_income", "index": 1},
        "series": {"name": "income_tax", "index": 1, "target": "baseline"},
        "x": [0, 50_000, 100_000],
        "y": [4, 5, 6],
    }
    assert set(run.series_by_target["baseline"]) == {
        "household_net_income",
        "income_tax",
    }


def test_build_axes_simulation_keeps_baseline_and_reform_separate(monkeypatch):
    _patch_runtime(monkeypatch)

    run = axes.build_axes_simulation(
        people=[{"age": 30}, {"age": 31}],
        benunit={},
        household={},
        year=2026,
        reform={"gov.example": 1},
        axis={
            "name": "employment_income",
            "index": 1,
            "min": 0,
            "max": 100_000,
            "count": 3,
        },
        outputs=["household_net_income"],
    )

    assert run.metadata()["targets"] == ["baseline", "reform"]
    assert run.get_series(
        variable="household_net_income",
        target="baseline",
    )["y"] == [20_000, 50_000, 80_000]
    assert run.get_series(
        variable="household_net_income",
        target="reform",
    )["y"] == [20_100, 50_100, 80_100]


@pytest.mark.parametrize(
    ("axis", "message"),
    [
        (
            {
                "name": "employment_income",
                "min": 0,
                "max": 100,
                "count": 1,
            },
            "axis.count",
        ),
        (
            {
                "name": "employment_income",
                "min": 100,
                "max": 0,
                "count": 3,
            },
            "axis.min",
        ),
        (
            {
                "name": "employment_income",
                "index": 2,
                "min": 0,
                "max": 100,
                "count": 3,
            },
            "axis.index",
        ),
        (
            {
                "name": "region",
                "min": 0,
                "max": 100,
                "count": 3,
            },
            "must be numeric",
        ),
        (
            {
                "name": "employment_income",
                "min": 0,
                "max": 100,
                "count": 3,
                "period": 2026,
            },
            "unsupported fields",
        ),
    ],
)
def test_axis_validation_rejects_invalid_contract(monkeypatch, axis, message):
    monkeypatch.setattr(axes, "uk_model_version", _model)

    with pytest.raises(ValueError, match=message):
        axes._normalise_axis(axis, people=[{"age": 30}])


@pytest.mark.parametrize(
    ("outputs", "message"),
    [
        ([], "between 1 and 5"),
        (
            [
                "income_tax",
                "household_net_income",
                "employment_income",
                "income_tax_2",
                "income_tax_3",
                "income_tax_4",
            ],
            "between 1 and 5",
        ),
        (["income_tax", "income_tax"], "duplicate"),
        (["region"], "must be numeric"),
        (["not_a_variable"], "Unknown household variable"),
    ],
)
def test_output_validation_rejects_invalid_contract(monkeypatch, outputs, message):
    monkeypatch.setattr(axes, "uk_model_version", _model)

    with pytest.raises(ValueError, match=message):
        axes._normalise_outputs(outputs)


def test_get_series_rejects_unavailable_target_output_and_index(monkeypatch):
    _patch_runtime(monkeypatch)
    run = axes.build_axes_simulation(
        people=[{"age": 30}, {"age": 31}],
        benunit={},
        household={},
        year=2026,
        reform=None,
        axis={
            "name": "employment_income",
            "index": 1,
            "min": 0,
            "max": 100_000,
            "count": 3,
        },
        outputs=["income_tax"],
    )

    with pytest.raises(ValueError, match="Target 'reform' is not available"):
        run.get_series(variable="income_tax", target="reform")
    with pytest.raises(ValueError, match="was not selected"):
        run.get_series(variable="household_net_income")
    with pytest.raises(ValueError, match="out of range"):
        run.get_series(variable="income_tax", index=2)


def test_axes_calculator_length_must_match_requested_count(monkeypatch):
    _patch_runtime(monkeypatch)

    with pytest.raises(ValueError, match="unexpected point count"):
        axes.build_axes_simulation(
            people=[{"age": 30}, {"age": 31}],
            benunit={},
            household={},
            year=2026,
            reform=None,
            axis={
                "name": "employment_income",
                "index": 1,
                "min": 0,
                "max": 100_000,
                "count": 2,
            },
            outputs=["income_tax"],
        )


@requires_policyengine_py
def test_policyengine_py_runs_a_401_point_axis():
    from chat.orchestrator import (
        MAX_TOOL_RESULT_CHARS,
        _serialise_tool_result_for_model,
    )

    run = axes.build_axes_simulation(
        people=[{"age": 30}],
        benunit={},
        household={},
        year=2026,
        reform=None,
        axis={
            "name": "employment_income",
            "min": 0,
            "max": 100_000,
            "count": 401,
        },
        outputs=["household_net_income"],
    )

    series = run.get_series(variable="household_net_income")
    assert len(series["x"]) == 401
    assert len(series["y"]) == 401
    assert series["x"][0] == 0
    assert series["x"][-1] == 100_000
    result_json = _serialise_tool_result_for_model(series)
    assert len(result_json) < MAX_TOOL_RESULT_CHARS
    assert json.loads(result_json) == series
