from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from analysis.common import AnalysisError, AnalysisErrorCode
from analysis.execution_engine import (
    CallbackExecutionControl,
    ExecutionCancelled,
    ExecutionCompleted,
    ExecutionEngine,
    ExecutionFailed,
    ExecutionRequest,
    OperationCompleted,
    OperationStarted,
)
from analysis.executor import ExecutionOutcome, OperationEvent
from analysis.models import ExecutionCompletion, ExecutionMode, ExecutionRecord
from analysis.operations import default_operation_catalogue
from analysis_helpers import plan_and_records
from tools.context import TurnResultStore


def _request(*, kind: str = "society", fields=None, outputs=("budgetary_impact",)):
    semantic, bound, plan, _state, attempt = plan_and_records(
        kind,
        fields=fields,
        outputs=outputs,
    )
    progress = []
    result_store = TurnResultStore(
        default_execution_id=str(attempt.execution_id)
    )
    request = ExecutionRequest(
        plan=plan,
        attempt=attempt,
        token="token",
        revision=semantic,
        bound_request=bound,
        control=CallbackExecutionControl(
            attempt_verifier=lambda _execution_id, _token: attempt,
            cancellation_probe=lambda: False,
            progress_sink=progress.append,
        ),
    )
    return request, progress, result_store


def _outcome(request, status="completed", events=()):
    return ExecutionOutcome(
        completion=ExecutionCompletion(
            execution_id=request.attempt.execution_id,
            status=status,
            error_code=(
                AnalysisErrorCode.EXECUTION_FAILED.value
                if status == "failed"
                else None
            ),
        ),
        record=ExecutionRecord(
            execution_id=request.attempt.execution_id,
            plan_id=request.plan.plan_id,
        ),
        envelopes=(),
        events=events,
    )


def test_engine_selects_standard_strategy_and_reports_typed_progress():
    request, reported, result_store = _request()
    calls = []

    def standard(**arguments):
        calls.append(arguments)
        assert arguments["context"].result_store is result_store
        arguments["on_event"](
            OperationEvent(
                kind="start",
                operation="run_society_simulation",
                step_id="simulation",
                arguments={"year": 2026},
            )
        )
        arguments["on_event"](
            OperationEvent(
                kind="complete",
                operation="run_society_simulation",
                step_id="simulation",
                status="success",
                output={"status": "success"},
            )
        )
        return _outcome(request)

    def exploratory(**_arguments):
        raise AssertionError("standard plan must not use exploratory strategy")

    result = ExecutionEngine(
        standard_strategy=standard,
        exploratory_strategy=exploratory,
        operation_catalogue=default_operation_catalogue(),
        result_store_factory=lambda _execution_id: result_store,
    ).execute(request)

    assert isinstance(result, ExecutionCompleted)
    assert len(calls) == 1
    assert isinstance(reported[0], OperationStarted)
    assert isinstance(reported[1], OperationCompleted)
    assert result.progress == tuple(reported)


def test_engine_selects_exploratory_strategy_and_passes_only_supplied_adapter():
    request, _reported, _store = _request(
        kind="exploratory",
        fields={"objective": "trace effects"},
    )
    adapter = SimpleNamespace(messages=object())
    request = replace(request, exploratory_model_adapter=adapter)
    clients = []

    def standard(**_arguments):
        raise AssertionError("exploratory plan must not use standard strategy")

    def exploratory(**arguments):
        clients.append(arguments["client"])
        return _outcome(request)

    result = ExecutionEngine(
        standard_strategy=standard,
        exploratory_strategy=exploratory,
    ).execute(request)

    assert isinstance(result, ExecutionCompleted)
    assert clients == [adapter]


@pytest.mark.parametrize(
    ("status", "result_type"),
    [
        ("failed", ExecutionFailed),
        ("cancelled", ExecutionCancelled),
    ],
)
def test_engine_returns_discriminated_non_success_results(status, result_type):
    request, _reported, _store = _request()

    result = ExecutionEngine(
        standard_strategy=lambda **_arguments: _outcome(request, status=status)
    ).execute(request)

    assert isinstance(result, result_type)
    assert result.kind == status


def test_engine_rejects_non_executable_plan_mode_before_strategy_call():
    request, _reported, _store = _request()
    request = replace(
        request,
        plan=request.plan.model_copy(update={"mode": ExecutionMode.EXPLANATION}),
    )

    with pytest.raises(AnalysisError) as raised:
        ExecutionEngine().execute(request)

    assert raised.value.code == AnalysisErrorCode.PLAN_INVALID
