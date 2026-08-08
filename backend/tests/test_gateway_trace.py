from gateway.assessment import ReformAssessment, ValidatedParameterBinding
from gateway.execution import GatewayExecutionPlan, ExecutionInput
from gateway.policy import GatingReason, SlotFact
from gateway.runtime import GatewayVerdict
from gateway.trace import gateway_trace_from_verdict


def test_trace_contains_route_inputs_defaults_and_reform_assessment():
    assessment = ReformAssessment(
        reform={"gov.example.rate": 0.21},
        summary="Increase the example rate",
        confidence=91,
        parameter_bindings=(
            ValidatedParameterBinding(
                "gov.example.rate",
                "Example rate",
                "example rate",
            ),
        ),
        alternatives=(),
        search_queries=("example rate",),
        catalogue_version="2026.8",
    )
    verdict = GatewayVerdict(
        outcome="ready",
        route="compute",
        tool="run_society_simulation",
        slots=[
            SlotFact("year", "default", value="2026"),
            SlotFact(
                "output",
                "prompt",
                kind="output",
                value="budgetary_impact",
            ),
        ],
        gating_reasons=[
            GatingReason("catalogue_choice", "reform", ("Example rate",))
        ],
        reform_assessment=assessment,
        catalogue_recovery_used=True,
        execution_plan=GatewayExecutionPlan(
            target_tool="compute_budgetary_impact",
            prerequisites=("run_society_simulation",),
            inputs=(ExecutionInput("year", "2026", "default"),),
            conventions=(),
            parameter_bindings=assessment.parameter_bindings,
            approved_reform=dict(assessment.reform),
        ),
        proposal_resumed=True,
    )

    trace = gateway_trace_from_verdict(verdict)

    assert trace.selected_tool == "run_society_simulation"
    assert trace.target_tool == "compute_budgetary_impact"
    assert trace.slots[0].source == "default"
    assert trace.gating_reasons[0].options == ("Example rate",)
    assert trace.defaults_applied == {"year": 2026}
    assert trace.reform_confidence == 91
    assert trace.reform_summary == "Increase the example rate"
    assert trace.reform_search_queries == ("example rate",)
    assert trace.catalogue_version == "2026.8"
    assert trace.parameter_bindings[0].label == "Example rate"
    assert trace.catalogue_recovery_used is True
    assert trace.proposal_resumed is True


def test_trace_is_none_without_a_completed_gateway_verdict():
    assert gateway_trace_from_verdict(None) is None
