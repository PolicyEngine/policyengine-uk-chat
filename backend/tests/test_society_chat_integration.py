"""SQL-backed multi-turn regressions for population analysis conversations."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
import json

import pytest
from sqlmodel import SQLModel, create_engine

from capabilities.artifacts import SocietyAnalysisResultRef
from capabilities.composition import compose_runtime
from capabilities.household import (
    AssembleHouseholdCandidateTool,
    HouseholdAnalysisCapability,
    HouseholdAnalysisDraft,
)
from capabilities.household_input import (
    AmountFrequency,
    HouseholdEvidence,
    HouseholdEvidenceResult,
    PeriodicAmount,
    PersonEvidence,
)
from capabilities.policy_reform import (
    PolicyReformCapability,
    PolicyReformInput,
    ReformMeaning,
    ReformResolutionDecision,
    ReformResolutionKind,
    ResolveReformTool,
)
from capabilities.relevance import (
    AssessRelevanceTool,
    ConversationRelevanceCapability,
    RelevanceAssessment,
    RelevanceResult,
)
from capabilities.society import SocietyAnalysisCapability, SocietyAnalysisInput
import capabilities.society as society_module
from capabilities.tracing import InvocationTracer
from chat.artifact_context import RepositoryArtifactSummarySource
from chat.capability_service import ChatTurnService
from chat.events import TurnCompleted
from chat.model_port import ConversationModelResponse, ModelCapabilityCall
from chat.turn_input import ChatTurnInput
from engine.py_runtime import DatasetSpec
from persistence.capability_repository import (
    PartialInputRegistry,
    RepositoryArtifactAccess,
    SQLConversationCapabilityRepository,
)
from persistence.idempotency import SQLIdempotencyRepository
from persistence.trace_repository import SQLInvocationTraceRepository
from tools.analysis_support import (
    ExtractResultFindingsTool,
    SelectSupportedOutputsTool,
    VerifyNumericalResponseTool,
)
from tools.typed_dispatch import build_dispatch_tools


@dataclass(frozen=True)
class CapabilityAction:
    capability_id: str
    input: dict[str, object]


@dataclass(frozen=True)
class ConversationPath:
    name: str
    turns: tuple[str, ...]
    actions: tuple[CapabilityAction, ...]
    expected_society_runs: int


class RelevantAssessor:
    async def assess(self, request):
        del request
        return RelevanceAssessment(
            result=RelevanceResult.RELEVANT,
            explanation="supported",
        )


class FixedReformResolver:
    """Deterministic substitute for the model-backed catalogue selection step."""

    async def resolve(self, *, instruction, year, candidates):
        del instruction, year, candidates
        return ReformResolutionDecision(
            outcome=ReformResolutionKind.RESOLVED,
            summary="Set the UK basic income-tax rate to 22%.",
            reform={"gov.hmrc.income_tax.rates.uk[0].rate": 0.22},
            meaning=ReformMeaning(
                target="gov.hmrc.income_tax.rates.uk[0].rate",
                operation="set",
                value=0.22,
                unit="ratio",
                effective_date="2026-01-01",
                population="UK income-tax payers",
                jurisdiction="United Kingdom",
            ),
        )

    async def correct_representation(self, **kwargs):
        raise AssertionError(f"No representation correction expected: {kwargs}")


class FixedHouseholdAssembler:
    """Provide a complete household for the cross-scope conversation path."""

    async def assemble(self, **kwargs):
        del kwargs
        return HouseholdEvidenceResult(
            evidence=HouseholdEvidence(
                people=(
                    PersonEvidence(
                        age=35,
                        employment_income=PeriodicAmount(
                            amount=Decimal("50000"),
                            frequency=AmountFrequency.ANNUAL,
                        ),
                        sources={"age": "user", "employment_income": "user"},
                    ),
                ),
                rent=PeriodicAmount(
                    amount=Decimal("0"),
                    frequency=AmountFrequency.ANNUAL,
                ),
                council_tax=PeriodicAmount(
                    amount=Decimal("0"),
                    frequency=AmountFrequency.ANNUAL,
                ),
                sources={"rent": "user", "council_tax": "user"},
            )
        )


class ScriptedConversationModel:
    """Invoke one configured capability per user turn, then narrate typed outputs."""

    def __init__(self, actions: tuple[CapabilityAction, ...]) -> None:
        self._actions = actions
        self.call_count = 0
        self.requests = []

    async def respond(self, request):
        self.requests.append(request)
        last = request.messages[-1]
        if isinstance(last.get("content"), str):
            action = self._actions[self.call_count]
            self.call_count += 1
            return ConversationModelResponse(
                capability_calls=(
                    ModelCapabilityCall(
                        call_id=f"scripted-call-{self.call_count}",
                        capability_id=action.capability_id,
                        input=action.input,
                    ),
                ),
                model="scripted-population-chat-model",
            )

        result = json.loads(last["content"][0]["content"])
        if result["status"] != "completed":
            text = result.get("safe_message") or result.get("reason") or result.get(
                "prompt", "The calculation did not complete."
            )
        else:
            outputs = result["value"]["result"]["outputs"]
            text = "\n".join(
                f"- {item['label']}: {item['value']:g} {item['unit']}"
                for item in outputs
            )
        return ConversationModelResponse(
            text=text,
            model="scripted-population-chat-model",
            stop_reason="end_turn",
        )

    async def redraft_numerical(self, **kwargs):
        raise AssertionError(f"Typed output narration should verify: {kwargs}")


def _society_action(
    reform_instruction: str | None = None,
    *requested_outputs: str,
) -> CapabilityAction:
    payload: dict[str, object] = {}
    if reform_instruction is not None:
        payload["reform_instruction"] = reform_instruction
    if requested_outputs:
        payload["requested_outputs"] = requested_outputs
    return CapabilityAction("society_analysis", payload)


BASELINE_TO_REFORM = ConversationPath(
    name="baseline-to-reform",
    turns=(
        "Show the current-law society-wide result.",
        "Now raise the basic income-tax rate by 2 percentage points.",
        "Run that population analysis again for 2026.",
        "Include poverty in a fresh population calculation.",
        "Include inequality in another population calculation.",
        "Recalculate the budget and distributional effects.",
        "Run a new population calculation for the same reform.",
        "Check winners and losers again.",
        "Recalculate the income-decile effects.",
        "Give me one final society-wide run.",
    ),
    actions=(
        _society_action(),
        _society_action("Raise the basic income-tax rate by 2 percentage points"),
        _society_action("Raise the basic income-tax rate by 2 percentage points"),
        _society_action(
            "Raise the basic income-tax rate by 2 percentage points", "poverty"
        ),
        _society_action(
            "Raise the basic income-tax rate by 2 percentage points", "inequality"
        ),
        *(
            _society_action("Raise the basic income-tax rate by 2 percentage points")
            for _ in range(5)
        ),
    ),
    expected_society_runs=10,
)


HOUSEHOLD_TO_SOCIETY = ConversationPath(
    name="household-to-society",
    turns=(
        "I am 35 and earn £50,000 a year. How much tax do I pay?",
        "What's the society-wide impact of raising the Basic Rate by 2pp?",
        "Run the population calculation again and include poverty.",
        "Now include inequality in a fresh population run.",
        "Recalculate the population-wide winners and losers.",
        "Run another society-wide calculation for the same reform.",
        "Check the income-decile effects with a new population run.",
        "Recalculate the budgetary impact across the population.",
        "Run the complete population profile once more.",
        "Give me a final society-wide calculation for that reform.",
    ),
    actions=(
        CapabilityAction(
            "household_analysis",
            {
                "description": "A 35-year-old earning £50,000 annually",
                "requested_outputs": ("tax",),
            },
        ),
        _society_action("Raise the basic income-tax rate by 2 percentage points"),
        _society_action(
            "Raise the basic income-tax rate by 2 percentage points", "poverty"
        ),
        _society_action(
            "Raise the basic income-tax rate by 2 percentage points", "inequality"
        ),
        *(
            _society_action("Raise the basic income-tax rate by 2 percentage points")
            for _ in range(6)
        ),
    ),
    expected_society_runs=9,
)


POPULATION_OUTPUT_VARIANTS = ConversationPath(
    name="population-output-variants",
    turns=(
        "Run a population analysis of a 2 percentage point basic-rate increase.",
        "Add poverty to a new run.",
        "Add inequality to a new run.",
        "Add programme statistics to a new run.",
        "Run the default population profile again.",
        "Recalculate the fiscal impact.",
        "Recalculate winners and losers.",
        "Recalculate the decile impacts.",
        "Run all supported headline outputs again.",
        "Finish with another complete society-wide calculation.",
    ),
    actions=(
        _society_action("Raise the basic income-tax rate by 2 percentage points"),
        _society_action(
            "Raise the basic income-tax rate by 2 percentage points", "poverty"
        ),
        _society_action(
            "Raise the basic income-tax rate by 2 percentage points", "inequality"
        ),
        _society_action(
            "Raise the basic income-tax rate by 2 percentage points",
            "programme statistics",
        ),
        *(
            _society_action("Raise the basic income-tax rate by 2 percentage points")
            for _ in range(6)
        ),
    ),
    expected_society_runs=10,
)


PATHS = (BASELINE_TO_REFORM, HOUSEHOLD_TO_SOCIETY, POPULATION_OUTPUT_VARIANTS)


async def _not_cancelled() -> bool:
    return False


async def _collect(service, turn, context):
    return [
        event
        async for event in service.run(
            turn,
            is_cancelled=_not_cancelled,
            context=context,
        )
    ]


@pytest.mark.parametrize("path", PATHS, ids=lambda path: path.name)
def test_ten_user_turn_population_conversations(
    path,
    tmp_path,
    monkeypatch,
):
    from tools import typed_dispatch

    engine = create_engine(f"sqlite:///{tmp_path / f'{path.name}.sqlite'}")
    SQLModel.metadata.create_all(engine)
    tool_calls: list[tuple[str, dict[str, object]]] = []

    monkeypatch.setattr(
        society_module,
        "resolve_dataset",
        lambda: DatasetSpec(
            name="enhanced_frs_2024_25",
            title="Enhanced FRS 2024-25",
            uri="hf://example/enhanced_frs_2024_25.h5@1.56.16",
            data_package_name="policyengine-uk-data",
            data_package_version="1.56.16",
            revision="1.56.16",
            sha256="dataset-sha256",
            certification_basis="legacy_compatible_model_package",
            certified_for_model_version="2.90.2",
            row_level_access=False,
        ),
    )

    def execute(identifier, payload, context=None):
        tool_calls.append((identifier, payload))
        if identifier == "list_supported_outputs":
            return {
                "status": "success",
                "scope": "derivative",
                "outputs": [
                    {"scope": "derivative", "name": name}
                    for name in (
                        "budgetary_impact",
                        "program_statistics",
                        "decile_impacts",
                        "winners_losers",
                        "poverty",
                        "inequality",
                    )
                ],
            }
        if identifier == "list_reform_targets":
            return {
                "status": "success",
                "targets": [
                    {
                        "path": "gov.hmrc.income_tax.rates.uk[0].rate",
                        "label": "Basic rate",
                    }
                ],
            }
        if identifier == "get_parameter":
            return {
                "status": "success",
                "parameter": {
                    "path": payload["path"],
                    "label": "Basic rate",
                    "unit": "ratio",
                    "value": 0.20,
                },
            }
        if identifier == "validate_reform":
            return {
                "valid": True,
                "normalized_reform": payload["reform"],
            }
        if identifier == "run_society_simulation":
            result_id = context.result_store.put(
                "society_simulation",
                object(),
                {"year": payload["year"]},
            )
            return {
                "status": "success",
                "year": payload["year"],
                "result_id": result_id,
            }
        if identifier == "compute_budgetary_impact":
            return {
                "status": "success",
                "simulation_id": payload["simulation_id"],
                "net_budgetary_impact": 150_000_000,
                "result_id": "budget-request-local",
            }
        if identifier == "compute_winners_losers":
            return {
                "status": "success",
                "simulation_id": payload["simulation_id"],
                "winners": 10_000_000,
                "losers": 3_000_000,
                "unchanged": 54_000_000,
                "result_id": "winners-request-local",
            }
        if identifier == "compute_decile_impacts":
            return {
                "status": "success",
                "simulation_id": payload["simulation_id"],
                "decile_concept": payload["decile_concept"],
                "deciles": [
                    {
                        "decile": decile,
                        "absolute_change": decile * 10,
                        "relative_change": decile / 1_000,
                    }
                    for decile in range(1, 11)
                ],
                "result_id": "deciles-request-local",
            }
        if identifier == "compute_poverty_metrics":
            return {
                "status": "success",
                "simulation_id": payload["simulation_id"],
                "overall_rate": 0.18,
                "result_id": "poverty-request-local",
            }
        if identifier == "compute_inequality_metrics":
            return {
                "status": "success",
                "simulation_id": payload["simulation_id"],
                "gini": 0.31,
                "result_id": "inequality-request-local",
            }
        if identifier == "compute_program_breakdown":
            return {
                "status": "success",
                "simulation_id": payload["simulation_id"],
                "programmes": [
                    {"program": "income_tax", "change": 150_000_000}
                ],
                "result_id": "programmes-request-local",
            }
        if identifier == "get_variable":
            return {
                "status": "success",
                "variable": {
                    "name": payload["name"],
                    "label": payload["name"].replace("_", " ").title(),
                    "entity": "person",
                },
            }
        if identifier == "search_variables":
            return {"status": "success", "variables": []}
        if identifier == "validate_household":
            return {"valid": True, "year": payload["year"]}
        if identifier == "run_household_simulation":
            return {
                "status": "success",
                "year": payload["year"],
                "reform_applied": False,
                "income_tax": 7_486,
                "national_insurance": 2_994.40,
                "household_tax": 10_480.40,
                "result_id": "household-request-local",
            }
        raise AssertionError(f"Unexpected retained tool call: {identifier}")

    monkeypatch.setattr(typed_dispatch, "execute_tool", execute)

    partial_inputs = PartialInputRegistry()
    partial_inputs.register(
        "policy_reform",
        schema_version="1",
        model=PolicyReformInput,
    )
    partial_inputs.register(
        "household_analysis",
        schema_version="1",
        model=HouseholdAnalysisDraft,
    )
    partial_inputs.register(
        "society_analysis",
        schema_version="1",
        model=SocietyAnalysisInput,
    )
    capability_repository = SQLConversationCapabilityRepository(
        engine=engine,
        partial_inputs=partial_inputs,
    )
    artifacts = RepositoryArtifactAccess(capability_repository)
    trace_repository = SQLInvocationTraceRepository(engine=engine)
    composition = compose_runtime(
        tools=(
            *build_dispatch_tools(),
            AssessRelevanceTool(RelevantAssessor()),
            ResolveReformTool(FixedReformResolver()),
            AssembleHouseholdCandidateTool(FixedHouseholdAssembler()),
            SelectSupportedOutputsTool(),
            ExtractResultFindingsTool(),
            VerifyNumericalResponseTool(),
        ),
        capabilities=(
            ConversationRelevanceCapability(),
            PolicyReformCapability(),
            HouseholdAnalysisCapability(),
            SocietyAnalysisCapability(),
        ),
        tracer=InvocationTracer(sink=trace_repository),
    )
    model = ScriptedConversationModel(path.actions)
    service = ChatTurnService(
        executor=composition.executor,
        capabilities=composition.capabilities,
        model=model,
        idempotency=SQLIdempotencyRepository(engine=engine),
        artifact_summaries=RepositoryArtifactSummarySource(capability_repository),
    )

    async def run_path():
        transcript = []
        completed_turns = []
        for index, user_message in enumerate(path.turns, start=1):
            transcript.append({"role": "user", "content": user_message})
            context = composition.executor.context(
                request_id=f"request-{index}",
                conversation_id=path.name,
                turn_id=f"turn-{index}",
                is_cancelled=_not_cancelled,
                artifacts=artifacts,
            )
            events = await _collect(
                service,
                ChatTurnInput(
                    messages=list(transcript),
                    session_id=path.name,
                    turn_id=f"turn-{index}",
                    debug=True,
                ),
                context,
            )
            assert isinstance(events[-1], TurnCompleted)
            completed_turns.append(events[-1])
            transcript.append({"role": "assistant", "content": events[-1].content})
        return completed_turns

    completed_turns = asyncio.run(run_path())

    assert len(path.turns) == len(path.actions) == 10
    assert model.call_count == 10
    assert len(model.requests) == 20
    assert all(turn.content for turn in completed_turns)
    assert all(
        "calculation failed" not in turn.content.casefold()
        for turn in completed_turns
    )

    society_calls = [
        payload
        for identifier, payload in tool_calls
        if identifier == "run_society_simulation"
    ]
    assert len(society_calls) == path.expected_society_runs
    assert all(
        "people" not in payload and "household" not in payload
        for payload in society_calls
    )
    for derivative in (
        "compute_budgetary_impact",
        "compute_winners_losers",
        "compute_decile_impacts",
    ):
        assert sum(identifier == derivative for identifier, _ in tool_calls) == (
            path.expected_society_runs
        ), [identifier for identifier, _ in tool_calls]

    persisted_results = capability_repository.find_artifacts(
        path.name,
        SocietyAnalysisResultRef,
    )
    assert len(persisted_results) == path.expected_society_runs
    assert all(result.dataset_version == "1.56.16" for result in persisted_results)
    assert all(result.dataset is not None for result in persisted_results)
    assert all(
        result.dataset.logical_name == "enhanced_frs_2024_25"
        and result.dataset.title == "Enhanced FRS 2024-25"
        and result.dataset.data_package_name == "policyengine-uk-data"
        and result.dataset.data_package_version == "1.56.16"
        and result.dataset.revision == result.dataset_version
        for result in persisted_results
        if result.dataset is not None
    )

    if path is HOUSEHOLD_TO_SOCIETY:
        identifiers = [identifier for identifier, _ in tool_calls]
        assert identifiers.count("run_household_simulation") == 1
        assert identifiers.index("run_household_simulation") < identifiers.index(
            "run_society_simulation"
        )
