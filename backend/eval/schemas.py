"""Typed contracts for manual UK chat AI evaluations."""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from analysis.models import (
    AnalysisSessionState,
    ExecutionAttempt,
    PendingClarification,
    PersistedExecutionMetadata,
    SemanticRequestRevision,
)


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


class AnalysisTraceExpectation(StrictModel):
    route: Optional[
        Literal[
            "clarification",
            "explanation",
            "standard",
            "exploratory",
            "execution_question",
            "cancellation",
        ]
    ] = None
    outcome: Optional[str] = None
    binding_outcome: Optional[str] = None
    execution_mode: Optional[Literal["explanation", "standard", "exploratory"]] = None
    required_operations: List[str] = Field(default_factory=list)


class ToolLoopCase(CaseBase):
    suite: Literal["tool_loop"] = "tool_loop"
    prompt: str
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    charts_mode: bool = False
    expected_tools: List[ToolCallExpectation] = Field(default_factory=list)
    forbidden_tools: List[str] = Field(default_factory=list)
    expect: TextExpectation = Field(default_factory=TextExpectation)
    max_iterations: int = Field(default=4, ge=1, le=8)
    trials: int = Field(default=1, ge=1, le=10)
    pass_threshold: float = Field(default=1.0, ge=0.0, le=1.0)
    offline_responses: List[ModelTurn] = Field(default_factory=list)
    analysis_expect: Optional[AnalysisTraceExpectation] = None


class TurnInterpretationExpectation(StrictModel):
    candidate_contains: Dict[str, Any] = Field(default_factory=dict)
    reduced_revision_contains: Dict[str, Any] = Field(default_factory=dict)
    binding_outcome: Optional[
        Literal["explanation", "clarification", "unsupported", "failed", "ready"]
    ] = None
    plan_contains: Dict[str, Any] = Field(default_factory=dict)
    permitted_operations: List[str] = Field(default_factory=list)
    lifecycle_outcome: Optional[
        Literal[
            "conversation_advanced",
            "clarification_required",
            "request_rejected",
            "plan_ready",
            "cancellation_requested",
            "attempt_outcome",
            "turn_failed",
        ]
    ] = None
    public_outcome_category: Optional[
        Literal[
            "completed",
            "clarification",
            "unsupported",
            "failed",
            "cancelled",
            "conflict",
            "still_processing",
        ]
    ] = None
    response_outcome: Literal[
        "candidate_rejected",
        "plan_rejected",
        "revision_accepted",
        "needs_clarification",
        "unsupported",
        "explanation",
        "ready_standard",
        "ready_exploratory",
        "execution_question",
        "cancelled",
        "operation_rejected",
        "narration_rejected",
    ]
    error_code: Optional[str] = None


class TurnInterpretationCase(CaseBase):
    suite: Literal["turn_interpretation"] = "turn_interpretation"
    prompt: str
    turn_id: str
    initial_state: AnalysisSessionState
    active_revision: Optional[SemanticRequestRevision] = None
    active_clarification: Optional[PendingClarification] = None
    executions: List[PersistedExecutionMetadata] = Field(default_factory=list)
    recent_messages: List[Dict[str, Any]] = Field(default_factory=list)
    permitted_revision_ids: List[str] = Field(default_factory=list)
    offline_candidate: Optional[Dict[str, Any]] = None
    adversarial_operation_call: Optional[ModelToolCall] = None
    adversarial_narration_draft: Optional[Dict[str, Any]] = None
    expect: TurnInterpretationExpectation

    @model_validator(mode="before")
    @classmethod
    def upgrade_legacy_analysis_fixtures(cls, value):
        if not isinstance(value, dict):
            return value
        upgraded = dict(value)
        state = upgraded.get("initial_state")
        if isinstance(state, dict) and state.get("schema_version") == 1:
            state = dict(state)
            state["schema_version"] = 2
            state.setdefault("active_bound_request_id", None)
            state.setdefault("active_execution_id", None)
            state.setdefault("pending_plan_id", None)
            upgraded["initial_state"] = state
        revision = upgraded.get("active_revision")
        if isinstance(revision, dict) and revision.get("schema_version") == 1:
            revision = dict(revision)
            revision["schema_version"] = 2
            revision.pop("readiness", None)
            upgraded["active_revision"] = revision
        clarification = upgraded.get("active_clarification")
        if (
            isinstance(clarification, dict)
            and clarification.get("schema_version") == 1
        ):
            clarification = dict(clarification)
            clarification["schema_version"] = 2
            clarification.setdefault("target_contract", "legacy")
            clarification.setdefault(
                "choice_mode",
                "advisory" if clarification.get("permitted_choices") else "open",
            )
            upgraded["active_clarification"] = clarification
        executions = []
        for execution in upgraded.get("executions", []):
            if isinstance(execution, dict) and execution.get("schema_version") == 1:
                execution = dict(execution)
                execution["schema_version"] = 2
            executions.append(execution)
        upgraded["executions"] = executions
        return upgraded


EvalCase = (
    ToolContractCase
    | TrajectoryCase
    | AnswerCase
    | ToolLoopCase
    | TurnInterpretationCase
)


class CaseResult(StrictModel):
    id: str
    suite: str
    status: Literal["passed", "failed", "skipped"]
    score: float
    errors: List[str] = Field(default_factory=list)
    details: Dict[str, Any] = Field(default_factory=dict)


class EvalReport(StrictModel):
    mode: Literal["offline", "live", "deployed"]
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


class EvalUsage(StrictModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


class EvalToolTrace(StrictModel):
    tool_id: str
    name: str
    input: Dict[str, Any] = Field(default_factory=dict)
    status: Literal["pending", "success", "error"] = "pending"
    output: Any = None


class EvalAnalysisTrace(StrictModel):
    workflow_version: int
    update_kind: Optional[str] = None
    revision_relationship: Optional[str] = None
    inherited_fields: List[str] = Field(default_factory=list)
    cleared_fields: List[str] = Field(default_factory=list)
    binding_outcome: Optional[str] = None
    clarification_id: Optional[str] = None
    plan_id: Optional[str] = None
    plan_hash: Optional[str] = None
    execution_mode: Optional[str] = None
    permitted_operations: List[str] = Field(default_factory=list)
    step_status: List[List[str]] = Field(default_factory=list)
    conflict_count: int = 0
    interpretation_retries: int = 0
    model_usage: Dict[str, int] = Field(default_factory=dict)


class EvalChatResponse(StrictModel):
    status: Literal["completed", "failed"]
    content: str = ""
    session_id: str
    model: Optional[str] = None
    route: str = "compute"
    outcome: Optional[str] = None
    stop_reason: Optional[str] = None
    usage: EvalUsage = Field(default_factory=EvalUsage)
    tool_trace: List[EvalToolTrace] = Field(default_factory=list)
    analysis_trace: Optional[EvalAnalysisTrace] = None
