"""Typed contracts for manual UK chat AI evaluations."""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    contains: Dict[str, Any] = Field(default_factory=dict)
    chart_contains: Dict[str, Any] = Field(default_factory=dict)
    required_paths: List[str] = Field(default_factory=list)
    absent_paths: List[str] = Field(default_factory=list)
    error_contains: Optional[str] = None
    numeric: List[NumericExpectation] = Field(default_factory=list)


class TextExpectation(StrictModel):
    required: List[str] = Field(default_factory=list)
    forbidden: List[str] = Field(default_factory=list)
    forbidden_regex: List[str] = Field(default_factory=list)
    grounded_numbers: bool = False
    allowed_numbers: List[float] = Field(default_factory=list)
    number_tolerance: float = 0.01


class ToolCallExpectation(StrictModel):
    name: str
    input_contains: Dict[str, Any] = Field(default_factory=dict)
    required_input_paths: List[str] = Field(default_factory=list)
    absent_input_paths: List[str] = Field(default_factory=list)


class ModelToolCall(StrictModel):
    id: str = ""
    name: str
    input: Dict[str, Any] = Field(default_factory=dict)


class ModelTurn(StrictModel):
    text: str = ""
    tool_calls: List[ModelToolCall] = Field(default_factory=list)


class FrozenToolCall(StrictModel):
    name: str
    input: Dict[str, Any] = Field(default_factory=dict)
    output_fixture: Optional[str] = None
    output: Optional[Dict[str, Any]] = None


class CaseBase(StrictModel):
    id: str
    suite: str
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
    input: Dict[str, Any] = Field(default_factory=dict)
    expect: OutputExpectation = Field(default_factory=OutputExpectation)


class TrajectoryCase(CaseBase):
    suite: Literal["trajectory"] = "trajectory"
    prompt: str
    messages: List[Dict[str, Any]] = Field(default_factory=list)
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
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    charts_mode: bool = False
    expected_tools: List[ToolCallExpectation] = Field(default_factory=list)
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
    expected_outcome: Literal[
        "irrelevant", "out_of_scope", "partial", "needs_plan", "ready"
    ]
    expected_tool: Optional[str] = None
    forbidden_tool: Optional[str] = None
    expected_gating_slots: List[str] = Field(default_factory=list)
    expected_slots: List[SlotExpectation] = Field(default_factory=list)


EvalCase = ToolContractCase | TrajectoryCase | AnswerCase | ToolLoopCase | GatewayCase


class CaseResult(StrictModel):
    id: str
    suite: str
    status: Literal["passed", "failed", "skipped"]
    score: float
    errors: List[str] = Field(default_factory=list)
    details: Dict[str, Any] = Field(default_factory=dict)


class EvalReport(StrictModel):
    mode: Literal["offline", "live"]
    suites: List[str]
    provider: str
    model: Optional[str] = None
    git_sha: Optional[str] = None
    started_at: str
    finished_at: str
    results: List[CaseResult]

    @property
    def passed(self) -> int:
        return sum(1 for result in self.results if result.status == "passed")

    @property
    def failed(self) -> int:
        return sum(1 for result in self.results if result.status == "failed")

    @property
    def skipped(self) -> int:
        return sum(1 for result in self.results if result.status == "skipped")
