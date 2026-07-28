"""Typed contracts for UK chat AI evaluations."""

from typing import Annotated, Dict, List, Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    computed_field,
    model_validator,
)

JsonObjectValue = Dict[str, JsonValue]
EvalSuite = Literal["tool_contract", "trajectory", "answer", "tool_loop", "gateway"]
CaseStatus = Literal["passed", "failed", "skipped"]
GatewayOutcome = Literal[
    "irrelevant",
    "out_of_scope",
    "partial",
    "needs_plan",
    "ready",
]
GatewaySlotKind = Literal["tool_input", "output"]
GatewaySlotSource = Literal["prompt", "default", "assumed"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NumericExpectation(StrictModel):
    path: str
    equals: Optional[float] = None
    min: Optional[float] = None
    max: Optional[float] = None
    tolerance: float = 0.0


class CaseSource(StrictModel):
    package: str
    version: str
    path: str
    name: str


class CaseSkip(StrictModel):
    code: Literal["policyengine_py_coverage_gap"]
    reason: str = Field(min_length=1)
    remove_when: str = Field(min_length=1)


class OutputExpectation(StrictModel):
    contains: JsonObjectValue = Field(default_factory=dict)
    chart_contains: JsonObjectValue = Field(default_factory=dict)
    required_paths: List[str] = Field(default_factory=list)
    absent_paths: List[str] = Field(default_factory=list)
    error_contains: Optional[str] = None
    numeric: List[NumericExpectation] = Field(default_factory=list)


class TextExpectation(StrictModel):
    required: List[str] = Field(default_factory=list)
    forbidden: List[str] = Field(default_factory=list)
    forbidden_regex: List[str] = Field(default_factory=list)
    factual_neutrality: bool = False
    grounded_numbers: bool = False
    allowed_numbers: List[float] = Field(default_factory=list)
    number_tolerance: float = 0.01


class ToolResultSelector(StrictModel):
    tool_name: str
    occurrence: int = Field(default=1, ge=1)
    result_selection: Literal["occurrence", "last_successful"] = "occurrence"


class RequiredAnswerValue(ToolResultSelector):
    path: str
    tolerance: float = Field(default=0.01, ge=0)
    scale: float = 1.0
    required_context: List[str] = Field(default_factory=list)


class ToolResultExpectation(ToolResultSelector):
    expect: OutputExpectation = Field(default_factory=OutputExpectation)


class LiveTextExpectation(TextExpectation):
    required_any: List[List[str]] = Field(default_factory=list)
    required_values: List[RequiredAnswerValue] = Field(default_factory=list)
    allowed_derived_numbers: List[float] = Field(default_factory=list)


class ToolCallExpectation(StrictModel):
    name: str
    input_contains: JsonObjectValue = Field(default_factory=dict)
    required_input_paths: List[str] = Field(default_factory=list)
    absent_input_paths: List[str] = Field(default_factory=list)


class ModelToolCall(StrictModel):
    id: str = ""
    name: str
    input: JsonObjectValue = Field(default_factory=dict)


class ModelTurn(StrictModel):
    text: str = ""
    tool_calls: List[ModelToolCall] = Field(default_factory=list)


class FrozenToolCall(StrictModel):
    name: str
    input: JsonObjectValue = Field(default_factory=dict)
    output_fixture: Optional[str] = None
    output: Optional[JsonObjectValue] = None


class CaseBase(StrictModel):
    id: str
    suite: EvalSuite
    description: str
    tags: List[str] = Field(default_factory=list)
    requirements: List[str] = Field(default_factory=list)
    source: Optional[CaseSource] = None
    skip: Optional[CaseSkip] = None

    @model_validator(mode="after")
    def skipped_cases_have_source(self):
        if self.skip is not None and self.source is None:
            raise ValueError("skipped eval cases must include source metadata")
        return self


class ToolContractCase(CaseBase):
    suite: Literal["tool_contract"] = "tool_contract"
    tool_name: str
    input: JsonObjectValue = Field(default_factory=dict)
    expect: OutputExpectation = Field(default_factory=OutputExpectation)


class TrajectoryCase(CaseBase):
    suite: Literal["trajectory"] = "trajectory"
    prompt: str
    messages: List[JsonObjectValue] = Field(default_factory=list)
    charts_mode: bool = False
    expected_tools: List[ToolCallExpectation] = Field(default_factory=list)
    forbidden_tools: List[str] = Field(default_factory=list)
    offline_response: Optional[ModelTurn] = None


class AnswerCase(CaseBase):
    suite: Literal["answer"] = "answer"
    prompt: str
    tool_calls: List[FrozenToolCall] = Field(default_factory=list)
    expect: TextExpectation = Field(default_factory=TextExpectation)
    offline_response: Optional[ModelTurn] = None


class ToolLoopCase(CaseBase):
    suite: Literal["tool_loop"] = "tool_loop"
    prompt: str
    messages: List[JsonObjectValue] = Field(default_factory=list)
    charts_mode: bool = False
    expected_tools: List[ToolCallExpectation] = Field(default_factory=list)
    expected_tool_results: List[ToolResultExpectation] = Field(default_factory=list)
    forbidden_tools: List[str] = Field(default_factory=list)
    expect: TextExpectation = Field(default_factory=TextExpectation)
    max_iterations: int = Field(default=4, ge=1, le=8)
    offline_responses: List[ModelTurn] = Field(default_factory=list)


class SlotExpectation(StrictModel):
    slot: str
    source: Optional[Literal["prompt", "default", "assumed"]] = None
    gates: Optional[bool] = None  # whether this slot should trigger a question


class GatewayCase(CaseBase):
    suite: Literal["gateway"] = "gateway"
    prompt: str
    expected_outcome: GatewayOutcome
    expected_tool: Optional[str] = None
    forbidden_tool: Optional[str] = None
    expected_gating_slots: List[str] = Field(default_factory=list)
    expected_slots: List[SlotExpectation] = Field(default_factory=list)


class LiveTrajectoryCase(TrajectoryCase):
    offline_response: None = None

    @model_validator(mode="after")
    def require_live_model(self):
        if "live_model" not in self.requirements:
            raise ValueError("live trajectory cases require 'live_model'")
        return self


class LiveAnswerCase(AnswerCase):
    expect: LiveTextExpectation = Field(default_factory=LiveTextExpectation)
    offline_response: None = None

    @model_validator(mode="after")
    def require_live_model(self):
        if "live_model" not in self.requirements:
            raise ValueError("live answer cases require 'live_model'")
        return self


class LiveToolLoopCase(ToolLoopCase):
    expect: LiveTextExpectation = Field(default_factory=LiveTextExpectation)
    offline_responses: List[ModelTurn] = Field(default_factory=list, max_length=0)

    @model_validator(mode="after")
    def require_live_model(self):
        if "live_model" not in self.requirements:
            raise ValueError("live tool-loop cases require 'live_model'")
        return self


class LiveGatewayCase(GatewayCase):
    @model_validator(mode="after")
    def require_live_model(self):
        if "live_model" not in self.requirements:
            raise ValueError("live gateway cases require 'live_model'")
        return self


DeterministicEvalCase = ToolContractCase | TrajectoryCase | AnswerCase | ToolLoopCase
LiveEvalCase = (
    LiveTrajectoryCase | LiveAnswerCase | LiveToolLoopCase | LiveGatewayCase
)
EvalCase = DeterministicEvalCase | LiveEvalCase


class ToolContractDetails(StrictModel):
    kind: Literal["tool_contract"] = "tool_contract"
    output: JsonObjectValue = Field(default_factory=dict)


class TrajectoryDetails(StrictModel):
    kind: Literal["trajectory"] = "trajectory"
    text: str = ""
    tool_calls: List[ModelToolCall] = Field(default_factory=list)


class AnswerDetails(StrictModel):
    kind: Literal["answer"] = "answer"
    text: str = ""


class ExecutedToolResult(StrictModel):
    name: str
    input: JsonObjectValue = Field(default_factory=dict)
    output: JsonObjectValue = Field(default_factory=dict)


class ToolLoopDetails(StrictModel):
    kind: Literal["tool_loop"] = "tool_loop"
    text: str = ""
    tool_calls: List[ModelToolCall] = Field(default_factory=list)
    tool_results: List[ExecutedToolResult] = Field(default_factory=list)


class GatewaySlotDetails(StrictModel):
    name: str
    kind: GatewaySlotKind
    source: GatewaySlotSource
    value: JsonValue = None


class GatewayDetails(StrictModel):
    kind: Literal["gateway"] = "gateway"
    outcome: GatewayOutcome
    tool: Optional[str] = None
    gating_slots: List[str] = Field(default_factory=list)
    unmodellable_outputs: List[str] = Field(default_factory=list)
    slots: List[GatewaySlotDetails] = Field(default_factory=list)


CaseResultDetails = Annotated[
    ToolContractDetails
    | TrajectoryDetails
    | AnswerDetails
    | ToolLoopDetails
    | GatewayDetails,
    Field(discriminator="kind"),
]


class CaseResult(StrictModel):
    id: str
    suite: EvalSuite
    trial: int = Field(default=1, ge=1)
    model: Optional[str] = None
    status: CaseStatus
    score: float
    errors: List[str] = Field(default_factory=list)
    details: Optional[CaseResultDetails] = None


class EvalReport(StrictModel):
    mode: Literal["offline", "live"]
    suites: List[EvalSuite]
    provider: str
    model: Optional[str] = None
    git_sha: Optional[str] = None
    started_at: str
    finished_at: str
    results: List[CaseResult]

    def _model_results(self) -> List[CaseResult]:
        return [
            result
            for result in self.results
            if result.suite in {"trajectory", "answer", "tool_loop", "gateway"}
        ]

    @computed_field
    @property
    def passed(self) -> int:
        return sum(1 for result in self.results if result.status == "passed")

    @computed_field
    @property
    def failed(self) -> int:
        return sum(1 for result in self.results if result.status == "failed")

    @computed_field
    @property
    def skipped(self) -> int:
        return sum(1 for result in self.results if result.status == "skipped")

    @computed_field
    @property
    def trial_count(self) -> int:
        return max((result.trial for result in self._model_results()), default=0)

    @computed_field
    @property
    def pass_at_1(self) -> float:
        first_trials = [
            result
            for result in self._model_results()
            if result.trial == 1 and result.status != "skipped"
        ]
        if not first_trials:
            return 0.0
        return sum(
            result.status == "passed" for result in first_trials
        ) / len(first_trials)

    @computed_field
    @property
    def pass_all_trials(self) -> float:
        by_case: Dict[tuple[str, str], List[CaseResult]] = {}
        for result in self._model_results():
            if result.status == "skipped":
                continue
            by_case.setdefault((result.suite, result.id), []).append(result)
        if not by_case:
            return 0.0
        return sum(
            len(case_results) == self.trial_count
            and all(result.status == "passed" for result in case_results)
            for case_results in by_case.values()
        ) / len(by_case)
