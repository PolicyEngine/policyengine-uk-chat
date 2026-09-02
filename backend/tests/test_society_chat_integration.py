"""SQL-backed multi-turn regressions for population analysis conversations."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
import json
from types import SimpleNamespace

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
    AnthropicReformCandidateResolver,
    PolicyReformCapability,
    PolicyReformInput,
    ReformMeaning,
    ReformResolutionDecision,
    ReformResolutionKind,
    ResolveReformTool,
)
import capabilities.policy_reform as policy_reform_module
from capabilities.relevance import (
    AssessRelevanceTool,
    ConversationRelevanceCapability,
    RelevanceAssessment,
    RelevanceResult,
)
from capabilities.society import (
    SOCIETY_EQUIVALISED_DECILE_REPORTING_NOTE,
    SOCIETY_HOUSEHOLD_INCOME_REPORTING_NOTE,
    SOCIETY_INEQUALITY_HBAI_REPORTING_NOTE,
    SOCIETY_POVERTY_HBAI_REPORTING_NOTE,
    SocietyAnalysisCapability,
    SocietyAnalysisInput,
)
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
    expected_compute_tools: frozenset[str]
    use_model_backed_reform: bool = False


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
                parameter_path="gov.hmrc.income_tax.rates.uk[0].rate",
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


class SchemaConstrainedResolverMessages:
    """Return a valid Basic Rate reform through the production resolver adapter."""

    def __init__(self) -> None:
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        assert "meaning.parameter_path" in kwargs["system"]
        assert "friendly label" in kwargs["system"]
        schema = kwargs["tools"][0]["input_schema"]
        meaning_properties = schema["$defs"]["ReformMeaning"]["properties"]
        assert "target" not in meaning_properties
        assert meaning_properties["parameter_path"]["enum"] == [
            "gov.hmrc.income_tax.rates.uk[0].rate",
            "gov.hmrc.income_tax.rates.savings.basic",
        ]
        request = json.loads(kwargs["messages"][0]["content"])
        assert request["instruction"] == "raising the Basic Rate 2pp"
        assert request["representation_correction"] is None
        return SimpleNamespace(
            content=[
                SimpleNamespace(
                    type="tool_use",
                    name="submit_reform_resolution",
                    input={
                        "outcome": "resolved",
                        "summary": "Raise the UK basic income-tax rate to 22%.",
                        "reform": {
                            "gov.hmrc.income_tax.rates.uk[0].rate": 0.22,
                        },
                        "meaning": {
                            "parameter_path": (
                                "gov.hmrc.income_tax.rates.uk[0].rate"
                            ),
                            "operation": "increase",
                            "value": 0.02,
                            "unit": "percentage points",
                            "effective_date": "2026-01-01",
                            "population": "UK income-tax payers",
                            "jurisdiction": "United Kingdom",
                        },
                    },
                )
            ],
            usage=SimpleNamespace(
                input_tokens=100,
                output_tokens=50,
                cache_creation_input_tokens=0,
                cache_read_input_tokens=0,
            ),
        )


class SchemaConstrainedResolverClient:
    def __init__(self) -> None:
        self.messages = SchemaConstrainedResolverMessages()


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


def _society_derivative_result(identifier, payload):
    common = {
        "status": "success",
        "simulation_id": payload["simulation_id"],
        "result_id": f"{identifier}-request-local",
    }
    if identifier == "compute_budgetary_impact":
        return {
            **common,
            "tax_revenue": {
                "baseline": 500_000_000,
                "reform": 620_000_000,
                "change": 120_000_000,
            },
            "benefit_spending": {
                "baseline": 250_000_000,
                "reform": 270_000_000,
                "change": 20_000_000,
            },
            "net_budgetary_impact": 100_000_000,
        }
    if identifier == "compute_decile_impacts":
        decile_concept = payload["decile_concept"]
        income_variable = (
            "equiv_hbai_household_net_income"
            if decile_concept == "equivalised_hbai_net_income"
            else "household_net_income"
        )
        decile_variable = (
            "household_wealth_decile" if decile_concept == "wealth" else None
        )
        grouping_label = {
            "household_net_income": "Household net income decile",
            "equivalised_hbai_net_income": "Equivalised HBAI net income decile",
            "wealth": "Wealth decile",
        }[decile_concept]
        return {
            **common,
            "decile_concept": decile_concept,
            "basis": "wealth" if decile_concept == "wealth" else "income",
            "income_variable": income_variable,
            "decile_variable": decile_variable,
            "grouping_variable": decile_variable or income_variable,
            "entity": "household",
            "quantiles": 10,
            "measure_label": (
                "equivalised HBAI net income"
                if decile_concept == "equivalised_hbai_net_income"
                else "household net income"
            ),
            "grouping_label": grouping_label,
            "deciles": [
                {
                    "decile": decile,
                    "baseline_mean": decile * 10_000,
                    "reform_mean": decile * 10_000 - decile * 10,
                    "absolute_change": -decile * 10,
                    "relative_change": -0.1,
                    "count_better_off": 0,
                    "count_worse_off": decile * 100_000,
                    "count_no_change": (11 - decile) * 100_000,
                }
                for decile in range(1, 11)
            ],
        }
    if identifier == "compute_winners_losers":
        decile_metadata = _society_derivative_result(
            "compute_decile_impacts",
            payload,
        )
        return {
            **common,
            **{
                key: decile_metadata[key]
                for key in (
                    "decile_concept",
                    "basis",
                    "income_variable",
                    "decile_variable",
                    "grouping_variable",
                    "entity",
                    "quantiles",
                    "measure_label",
                    "grouping_label",
                )
            },
            "deciles": [
                {
                    "decile": decile,
                    "lose_more_than_5pct": 0.01,
                    "lose_less_than_5pct": 0.59,
                    "no_change": 0.4,
                    "gain_less_than_5pct": 0,
                    "gain_more_than_5pct": 0,
                }
                for decile in range(11)
            ],
        }
    if identifier == "compute_poverty_metrics":
        return {
            **common,
            "rates": [
                {
                    "poverty_type": poverty_type,
                    "group": group,
                    "baseline_rate": 0.2,
                    "reform_rate": 0.19,
                    "rate_change": -0.01,
                    "relative_change": -0.05,
                    "baseline_headcount": 1_000_000,
                    "reform_headcount": 950_000,
                }
                for poverty_type in (
                    "absolute_ahc",
                    "absolute_bhc",
                    "relative_ahc",
                    "relative_bhc",
                )
                for group in ("adult", "all", "child", "senior")
            ],
        }
    if identifier == "compute_inequality_metrics":
        return {
            **common,
            "metrics": {
                metric: {
                    "baseline": 0.3,
                    "reform": 0.29,
                    "change": -0.01,
                    "relative_change": -1 / 30,
                }
                for metric in (
                    "gini",
                    "top_10_share",
                    "top_1_share",
                    "bottom_50_share",
                )
            },
        }
    if identifier == "compute_program_breakdown":
        return {
            **common,
            "programs": [
                {
                    "program": "income_tax",
                    "entity": "person",
                    "is_tax": True,
                    "baseline_total": 500_000_000,
                    "reform_total": 620_000_000,
                    "change": 120_000_000,
                    "baseline_count": 30_000_000,
                    "reform_count": 31_000_000,
                    "winners": 0,
                    "losers": 20_000_000,
                }
            ],
            "net_budgetary_impact": 100_000_000,
        }
    raise AssertionError(f"Unexpected society derivative: {identifier}")


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
            lines = []
            for item in outputs:
                if item["unit"] in {"people", "households"}:
                    continue
                if item["output_id"] == "winners_losers":
                    decile = next(
                        dimension["value"]
                        for dimension in item["dimensions"]
                        if dimension["name"] == "decile"
                    )
                    if decile == "overall":
                        continue
                    lines.append(
                        f"- Decile {decile}, {item['label']}: "
                        f"{item['value'] * 100:g}%"
                    )
                    continue
                lines.append(f"- {item['label']}: {item['value']:g} {item['unit']}")
            text = "\n".join(lines)
            income_reporting_notes = result["value"].get(
                "income_reporting_notes",
                [],
            )
            if income_reporting_notes:
                text = f"{' '.join(income_reporting_notes)}\n\n{text}"
        return ConversationModelResponse(
            text=text,
            model="scripted-population-chat-model",
            stop_reason="end_turn",
        )

    async def redraft_numerical(self, **kwargs):
        raise AssertionError(f"Typed output narration should verify: {kwargs}")

    async def review_assessment_language(self, **kwargs):
        raise AssertionError(
            f"Neutral scripted narration should not need review: {kwargs}"
        )


def _society_action(
    reform_instruction: str | None = None,
    *requested_outputs: str,
    decile_concept: str = "household_net_income",
) -> CapabilityAction:
    payload: dict[str, object] = {}
    if reform_instruction is not None:
        payload["reform_instruction"] = reform_instruction
    if requested_outputs:
        payload["requested_outputs"] = requested_outputs
    if decile_concept != "household_net_income":
        payload["decile_concept"] = decile_concept
    return CapabilityAction("society_analysis", payload)


BASELINE_TO_REFORM = ConversationPath(
    name="baseline-to-reform",
    turns=(
        "Show the current-law society-wide result.",
        "Now raise the basic income-tax rate by 2 percentage points.",
        "Run that population analysis again for 2026.",
        "Include poverty in a fresh population calculation.",
        "Run the complete default profile again.",
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
        _society_action("Raise the basic income-tax rate by 2 percentage points"),
        *(
            _society_action("Raise the basic income-tax rate by 2 percentage points")
            for _ in range(5)
        ),
    ),
    expected_society_runs=10,
    expected_compute_tools=frozenset(
        {
            "compute_budgetary_impact",
            "compute_winners_losers",
            "compute_decile_impacts",
            "compute_poverty_metrics",
        }
    ),
)


HOUSEHOLD_TO_SOCIETY = ConversationPath(
    name="household-to-society",
    turns=(
        "I am 35 and earn £50,000 a year. How much tax do I pay?",
        "What's the society-wide impact of raising the Basic Rate by 2pp?",
        "Run the population calculation again and include inequality.",
        "Now rerun the complete default population profile.",
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
            "Raise the basic income-tax rate by 2 percentage points", "inequality"
        ),
        *(
            _society_action("Raise the basic income-tax rate by 2 percentage points")
            for _ in range(7)
        ),
    ),
    expected_society_runs=9,
    expected_compute_tools=frozenset(
        {
            "compute_budgetary_impact",
            "compute_winners_losers",
            "compute_decile_impacts",
            "compute_inequality_metrics",
        }
    ),
)


POPULATION_OUTPUT_VARIANTS = ConversationPath(
    name="population-output-variants",
    turns=(
        "Run a population analysis of a 2 percentage point basic-rate increase.",
        "Add programme statistics to a new run.",
        "Run the default profile again.",
        "Recalculate the programme statistics.",
        "Run the default population profile again.",
        "Recalculate the fiscal impact.",
        "Recalculate winners and losers.",
        "Recalculate the decile impacts using equivalised HBAI net income.",
        "Run all supported headline outputs again.",
        "Finish with another complete society-wide calculation.",
    ),
    actions=(
        _society_action("Raise the basic income-tax rate by 2 percentage points"),
        _society_action(
            "Raise the basic income-tax rate by 2 percentage points",
            "programme statistics",
        ),
        _society_action("Raise the basic income-tax rate by 2 percentage points"),
        _society_action(
            "Raise the basic income-tax rate by 2 percentage points",
            "programme statistics",
        ),
        _society_action("Raise the basic income-tax rate by 2 percentage points"),
        _society_action("Raise the basic income-tax rate by 2 percentage points"),
        _society_action("Raise the basic income-tax rate by 2 percentage points"),
        _society_action(
            "Raise the basic income-tax rate by 2 percentage points",
            decile_concept="equivalised_hbai_net_income",
        ),
        _society_action("Raise the basic income-tax rate by 2 percentage points"),
        _society_action("Raise the basic income-tax rate by 2 percentage points"),
    ),
    expected_society_runs=10,
    expected_compute_tools=frozenset(
        {
            "compute_budgetary_impact",
            "compute_winners_losers",
            "compute_decile_impacts",
            "compute_program_breakdown",
        }
    ),
)


BASIC_RATE_PARAMETER_PATH_REGRESSION = ConversationPath(
    name="basic-rate-parameter-path-regression",
    turns=(
        "What's the impact of raising the Basic Rate 2pp on all of society?",
        "Run that complete population analysis again.",
        "Recalculate the population-wide fiscal impact.",
        "Run the winners-and-losers calculation again.",
        "Recalculate the income-decile impacts.",
        "Run another complete population calculation.",
        "Repeat the population analysis for the same reform.",
        "Calculate the complete default population profile again.",
        "Run one more society-wide calculation.",
        "Finish with a final complete population analysis.",
    ),
    actions=tuple(
        _society_action("raising the Basic Rate 2pp", "societal_impact")
        for _ in range(10)
    ),
    expected_society_runs=10,
    expected_compute_tools=frozenset(
        {
            "compute_budgetary_impact",
            "compute_winners_losers",
            "compute_decile_impacts",
        }
    ),
    use_model_backed_reform=True,
)


PATHS = (
    BASELINE_TO_REFORM,
    HOUSEHOLD_TO_SOCIETY,
    POPULATION_OUTPUT_VARIANTS,
    BASIC_RATE_PARAMETER_PATH_REGRESSION,
)
ALL_SOCIETY_COMPUTE_TOOLS = frozenset(
    {
        "compute_budgetary_impact",
        "compute_program_breakdown",
        "compute_decile_impacts",
        "compute_winners_losers",
        "compute_poverty_metrics",
        "compute_inequality_metrics",
    }
)


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
            targets = [
                {
                    "path": "gov.hmrc.income_tax.rates.uk[0].rate",
                    "label": "Basic rate",
                }
            ]
            if path.use_model_backed_reform:
                targets.append(
                    {
                        "path": "gov.hmrc.income_tax.rates.savings.basic",
                        "label": "Savings basic rate",
                    }
                )
            return {
                "status": "success",
                "targets": targets,
            }
        if identifier == "get_parameter":
            savings = payload["path"].endswith("savings.basic")
            return {
                "status": "success",
                "parameter": {
                    "path": payload["path"],
                    "label": "Savings basic rate" if savings else "Basic rate",
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
        if identifier.startswith("compute_"):
            return _society_derivative_result(identifier, payload)
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
    resolver_client = None
    reform_resolver = FixedReformResolver()
    if path.use_model_backed_reform:
        resolver_client = SchemaConstrainedResolverClient()
        monkeypatch.setattr(
            policy_reform_module,
            "get_async_client",
            lambda: resolver_client,
        )
        reform_resolver = AnthropicReformCandidateResolver()
    composition = compose_runtime(
        tools=(
            *build_dispatch_tools(),
            AssessRelevanceTool(RelevantAssessor()),
            ResolveReformTool(reform_resolver),
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
    assert all(
        "does not match" not in turn.content.casefold()
        for turn in completed_turns
    )
    if resolver_client is not None:
        assert len(resolver_client.messages.calls) == path.expected_society_runs

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

    compute_tools = [
        identifier
        for identifier, _payload in tool_calls
        if identifier.startswith("compute_")
    ]
    assert frozenset(compute_tools) == path.expected_compute_tools
    assert len(path.expected_compute_tools) >= 2

    society_turn_ids = {
        f"turn-{index}"
        for index, action in enumerate(path.actions, start=1)
        if action.capability_id == "society_analysis"
    }
    for index, turn in enumerate(completed_turns, start=1):
        if f"turn-{index}" not in society_turn_ids:
            continue
        assert "People better off" not in turn.content
        assert "People worse off" not in turn.content
        assert "People with no change" not in turn.content
        assert " people" not in turn.content
        assert " households" not in turn.content
        assert "Decile overall" not in turn.content
        assert "Decile 1, Share losing more than 5%: 1%" in turn.content
        action = path.actions[index - 1]
        if action.input.get("decile_concept") == "equivalised_hbai_net_income":
            assert SOCIETY_EQUIVALISED_DECILE_REPORTING_NOTE in turn.content
        else:
            assert SOCIETY_HOUSEHOLD_INCOME_REPORTING_NOTE in turn.content
        requested_outputs = tuple(action.input.get("requested_outputs", ()))
        if any("poverty" in output for output in requested_outputs):
            assert SOCIETY_POVERTY_HBAI_REPORTING_NOTE in turn.content
        if any("inequality" in output for output in requested_outputs):
            assert SOCIETY_INEQUALITY_HBAI_REPORTING_NOTE in turn.content
    traces = trace_repository.list_for_conversation(
        path.name,
        include_private=True,
    )
    assert not any(
        trace.turn_id in society_turn_ids
        and trace.identifier == "verify_numerical_response"
        for trace in traces
    )
    if path is BASIC_RATE_PARAMETER_PATH_REGRESSION:
        selections = [
            trace for trace in traces if trace.identifier == "select_supported_outputs"
        ]
        assert len(selections) == 10
        assert all(
            trace.debug_input == {"requested_outputs": ["societal_impact"]}
            and trace.debug_output == {
                "output_ids": [
                    "budgetary_impact",
                    "winners_losers",
                    "decile_impacts",
                ],
                "issues": [],
            }
            for trace in selections
        )

    persisted_results = capability_repository.find_artifacts(
        path.name,
        SocietyAnalysisResultRef,
    )
    assert len(persisted_results) == path.expected_society_runs
    if path is BASIC_RATE_PARAMETER_PATH_REGRESSION:
        assert all(not result.requested_output_issues for result in persisted_results)
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


def test_ten_turn_paths_collectively_cover_every_society_compute_tool():
    assert frozenset().union(
        *(path.expected_compute_tools for path in PATHS)
    ) == ALL_SOCIETY_COMPUTE_TOOLS
