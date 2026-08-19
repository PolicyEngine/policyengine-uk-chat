"""Versioned typed contracts for policy-analysis state and execution."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Any, Literal, NewType

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    TypeAdapter,
    field_validator,
)

from analysis.common import PLAN_SCHEMA_VERSION, WORKFLOW_SCHEMA_VERSION


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# These aliases keep identifiers distinct to static type checkers while retaining
# the string representation required by the existing HTTP and database contracts.
SessionId = NewType("SessionId", str)
RevisionId = NewType("RevisionId", str)
ClarificationId = NewType("ClarificationId", str)
BoundRequestId = NewType("BoundRequestId", str)
PlanId = NewType("PlanId", str)
ExecutionId = NewType("ExecutionId", str)
ResultId = NewType("ResultId", str)
TurnId = NewType("TurnId", str)


class WorkflowVersionedModel(FrozenModel):
    schema_version: int = WORKFLOW_SCHEMA_VERSION

    @field_validator("schema_version")
    @classmethod
    def supported_workflow_schema(cls, value: int) -> int:
        if value != WORKFLOW_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported workflow schema version {value}; "
                f"expected {WORKFLOW_SCHEMA_VERSION}"
            )
        return value


class PlanVersionedModel(FrozenModel):
    schema_version: int = PLAN_SCHEMA_VERSION

    @field_validator("schema_version")
    @classmethod
    def supported_plan_schema(cls, value: int) -> int:
        if value != PLAN_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported plan schema version {value}; expected {PLAN_SCHEMA_VERSION}"
            )
        return value


class EvidenceClaim(FrozenModel):
    quote: str = Field(min_length=1, max_length=1000)

    @field_validator("quote")
    @classmethod
    def non_blank_quote(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("evidence quote cannot be blank")
        return cleaned


class EvidenceReference(FrozenModel):
    quote: str
    message_sha256: str


class SetExactReform(FrozenModel):
    kind: Literal["set_exact"] = "set_exact"
    value: StrictBool | StrictInt | StrictFloat | StrictStr


class ChangeReformByAmount(FrozenModel):
    kind: Literal["change_by_amount"] = "change_by_amount"
    amount: StrictInt | StrictFloat


class ChangeReformByPercent(FrozenModel):
    kind: Literal["change_by_percent"] = "change_by_percent"
    percent: StrictInt | StrictFloat


class AbolishReform(FrozenModel):
    kind: Literal["abolish"] = "abolish"


class SetReformToggle(FrozenModel):
    kind: Literal["set_toggle"] = "set_toggle"
    value: StrictBool


class ApplyNamedReformTransformation(FrozenModel):
    kind: Literal["named_transformation"] = "named_transformation"
    identifier: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class DirectionOnlyReform(FrozenModel):
    kind: Literal["direction_only"] = "direction_only"
    direction: Literal["increase", "decrease", "uprate", "abolish"]


ReformInstruction = Annotated[
    SetExactReform
    | ChangeReformByAmount
    | ChangeReformByPercent
    | AbolishReform
    | SetReformToggle
    | ApplyNamedReformTransformation
    | DirectionOnlyReform,
    Field(discriminator="kind"),
]
REFORM_INSTRUCTION_ADAPTER = TypeAdapter(ReformInstruction)


class UnchangedPatch(FrozenModel):
    op: Literal["unchanged"] = "unchanged"


class SetPatch(FrozenModel):
    op: Literal["set"] = "set"
    value: Any
    evidence: EvidenceClaim


class ClearPatch(FrozenModel):
    op: Literal["clear"] = "clear"
    evidence: EvidenceClaim


FieldPatch = Annotated[
    UnchangedPatch | SetPatch | ClearPatch,
    Field(discriminator="op"),
]


class InheritOutputs(FrozenModel):
    op: Literal["inherit"] = "inherit"


class AddOutputs(FrozenModel):
    op: Literal["add"] = "add"
    outputs: tuple[str, ...] = Field(min_length=1)
    evidence: EvidenceClaim


class RemoveOutputs(FrozenModel):
    op: Literal["remove"] = "remove"
    outputs: tuple[str, ...] = Field(min_length=1)
    evidence: EvidenceClaim


class ReplaceOutputs(FrozenModel):
    op: Literal["replace"] = "replace"
    outputs: tuple[str, ...]
    evidence: EvidenceClaim


OutputPatch = Annotated[
    InheritOutputs | AddOutputs | RemoveOutputs | ReplaceOutputs,
    Field(discriminator="op"),
]


class CandidateField(FrozenModel):
    value: Any
    evidence: EvidenceClaim


class CandidateAnalysis(FrozenModel):
    analysis_kind: CandidateField
    fields: dict[str, CandidateField] = Field(default_factory=dict)
    outputs: tuple[str, ...] = ()
    output_evidence: EvidenceClaim | None = None


class StartRelationship(StrEnum):
    NEW = "new"
    RELATED = "related"
    UNRELATED = "unrelated"


class RevisionRelationship(StrEnum):
    CORRECTION = "correction"
    ALTERNATIVE = "alternative"
    ADDITIONAL_OUTPUT = "additional_output"


class StartAnalysis(FrozenModel):
    kind: Literal["start_analysis"] = "start_analysis"
    candidate: CandidateAnalysis
    relationship: StartRelationship = StartRelationship.NEW
    related_revision_id: str | None = None


class ReviseAnalysis(FrozenModel):
    kind: Literal["revise_analysis"] = "revise_analysis"
    base_revision_id: str
    patches: dict[str, FieldPatch] = Field(default_factory=dict)
    outputs: OutputPatch = Field(default_factory=InheritOutputs)
    relationship: RevisionRelationship


class AnswerClarification(FrozenModel):
    kind: Literal["answer_clarification"] = "answer_clarification"
    question_id: str
    answer: Any
    evidence: EvidenceClaim


class AskAboutExecution(FrozenModel):
    kind: Literal["ask_about_execution"] = "ask_about_execution"
    question: str = Field(min_length=1)
    evidence: EvidenceClaim
    execution_id: str | None = None


class CancelAnalysis(FrozenModel):
    kind: Literal["cancel_analysis"] = "cancel_analysis"
    request_revision_id: str | None = None


CandidateTurnUpdate = Annotated[
    StartAnalysis
    | ReviseAnalysis
    | AnswerClarification
    | AskAboutExecution
    | CancelAnalysis,
    Field(discriminator="kind"),
]
CANDIDATE_TURN_UPDATE_ADAPTER = TypeAdapter(CandidateTurnUpdate)


class ValidatedCandidateField(FrozenModel):
    value: Any
    evidence: EvidenceReference


class ValidatedCandidateAnalysis(FrozenModel):
    analysis_kind: ValidatedCandidateField
    fields: dict[str, ValidatedCandidateField] = Field(default_factory=dict)
    outputs: tuple[str, ...] = ()
    output_evidence: EvidenceReference | None = None


class ValidatedStartAnalysis(FrozenModel):
    kind: Literal["start_analysis"] = "start_analysis"
    candidate: ValidatedCandidateAnalysis
    relationship: StartRelationship = StartRelationship.NEW
    related_revision_id: RevisionId | None = None


class ValidatedUnchangedPatch(FrozenModel):
    op: Literal["unchanged"] = "unchanged"


class ValidatedSetPatch(FrozenModel):
    op: Literal["set"] = "set"
    value: Any
    evidence: EvidenceReference


class ValidatedClearPatch(FrozenModel):
    op: Literal["clear"] = "clear"
    evidence: EvidenceReference


ValidatedFieldPatch = Annotated[
    ValidatedUnchangedPatch | ValidatedSetPatch | ValidatedClearPatch,
    Field(discriminator="op"),
]


class ValidatedInheritOutputs(FrozenModel):
    op: Literal["inherit"] = "inherit"


class ValidatedAddOutputs(FrozenModel):
    op: Literal["add"] = "add"
    outputs: tuple[str, ...] = Field(min_length=1)
    evidence: EvidenceReference


class ValidatedRemoveOutputs(FrozenModel):
    op: Literal["remove"] = "remove"
    outputs: tuple[str, ...] = Field(min_length=1)
    evidence: EvidenceReference


class ValidatedReplaceOutputs(FrozenModel):
    op: Literal["replace"] = "replace"
    outputs: tuple[str, ...]
    evidence: EvidenceReference


ValidatedOutputPatch = Annotated[
    ValidatedInheritOutputs
    | ValidatedAddOutputs
    | ValidatedRemoveOutputs
    | ValidatedReplaceOutputs,
    Field(discriminator="op"),
]


class ValidatedReviseAnalysis(FrozenModel):
    kind: Literal["revise_analysis"] = "revise_analysis"
    base_revision_id: RevisionId
    patches: dict[str, ValidatedFieldPatch] = Field(default_factory=dict)
    outputs: ValidatedOutputPatch = Field(default_factory=ValidatedInheritOutputs)
    relationship: RevisionRelationship


class ValidatedAnswerClarification(FrozenModel):
    kind: Literal["answer_clarification"] = "answer_clarification"
    question_id: ClarificationId
    answer: Any
    evidence: EvidenceReference


class ValidatedAskAboutExecution(FrozenModel):
    kind: Literal["ask_about_execution"] = "ask_about_execution"
    question: str = Field(min_length=1)
    evidence: EvidenceReference
    execution_id: ExecutionId


class ValidatedCancelAnalysis(FrozenModel):
    kind: Literal["cancel_analysis"] = "cancel_analysis"
    request_revision_id: RevisionId | None = None


ValidatedTurnUpdate = Annotated[
    ValidatedStartAnalysis
    | ValidatedReviseAnalysis
    | ValidatedAnswerClarification
    | ValidatedAskAboutExecution
    | ValidatedCancelAnalysis,
    Field(discriminator="kind"),
]
VALIDATED_TURN_UPDATE_ADAPTER = TypeAdapter(ValidatedTurnUpdate)


class FieldProvenance(StrEnum):
    USER = "user"
    INHERITED = "inherited"
    DEFAULT = "default"
    CATALOGUE = "catalogue"
    RUNTIME = "runtime"
    BOOTSTRAP = "bootstrap"


class RequestField(FrozenModel):
    value: Any
    provenance: FieldProvenance
    evidence: EvidenceReference | None = None
    inherited_from_revision_id: str | None = None


class Invalidation(FrozenModel):
    field: str
    reason: str


class SemanticRequestRevision(WorkflowVersionedModel):
    revision_id: RevisionId
    session_id: SessionId
    revision_number: int = Field(ge=1)
    turn_id: TurnId
    base_revision_id: RevisionId | None = None
    relationship: str
    fields: dict[str, RequestField]
    outputs: tuple[str, ...] = ()
    invalidations: tuple[Invalidation, ...] = ()
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class BoundRequest(WorkflowVersionedModel):
    bound_request_id: BoundRequestId
    session_id: SessionId
    request_revision_id: RevisionId
    fields: dict[str, RequestField]
    outputs: tuple[str, ...] = ()
    output_producers: tuple[str, ...] = ()
    producer_outputs: tuple[str, ...] = ()
    capability_version: str
    catalogue_version: str
    engine_version: str
    country_package_version: str
    dataset_identifier: str
    plan_schema_version: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class WorkflowPhase(StrEnum):
    IDLE = "idle"
    AWAITING_CLARIFICATION = "awaiting_clarification"
    READY = "ready"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ClarificationChoiceMode(StrEnum):
    OPEN = "open"
    ADVISORY = "advisory"
    CLOSED = "closed"


class PendingClarification(WorkflowVersionedModel):
    question_id: ClarificationId
    session_id: SessionId
    request_revision_id: RevisionId
    target_field: str
    target_contract: str = "legacy"
    choice_mode: ClarificationChoiceMode = ClarificationChoiceMode.OPEN
    reason_code: str
    prompt: str
    permitted_choices: tuple[str, ...] = ()
    attempt_count: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ClarificationResolutionOutcome(StrEnum):
    ANSWERED = "answered"
    REPLACED = "replaced"
    SUPERSEDED = "superseded"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    UNSUPPORTED = "unsupported"


class ClarificationResolution(WorkflowVersionedModel):
    resolution_id: str
    session_id: str
    question_id: str
    request_revision_id: str
    resolving_turn_id: str
    outcome: ClarificationResolutionOutcome
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AnalysisSessionState(WorkflowVersionedModel):
    session_id: SessionId
    state_version: int = Field(default=0, ge=0)
    phase: WorkflowPhase = WorkflowPhase.IDLE
    active_revision_id: RevisionId | None = None
    active_bound_request_id: BoundRequestId | None = None
    active_clarification_id: ClarificationId | None = None
    active_plan_id: PlanId | None = None
    active_execution_id: ExecutionId | None = None
    pending_plan_id: PlanId | None = None
    latest_execution_id: ExecutionId | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ExecutionMode(StrEnum):
    EXPLANATION = "explanation"
    STANDARD = "standard"
    EXPLORATORY = "exploratory"


class PlanStatus(StrEnum):
    READY = "ready"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"


class ResultReference(FrozenModel):
    source_step_id: str
    expected_result_type: str | None = None


def _restore_result_references(value: Any) -> Any:
    if isinstance(value, dict):
        if (
            "source_step_id" in value
            and set(value).issubset({"source_step_id", "expected_result_type"})
            and isinstance(value["source_step_id"], str)
        ):
            return ResultReference.model_validate(value)
        return {key: _restore_result_references(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_restore_result_references(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_restore_result_references(item) for item in value)
    return value


class PlanStep(FrozenModel):
    step_id: str
    operation: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
    result_binding: str
    result_type: str

    @field_validator("arguments", mode="after")
    @classmethod
    def typed_result_references(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _restore_result_references(value)


class OperationConstraint(FrozenModel):
    operation: str
    fixed_arguments: dict[str, Any] = Field(default_factory=dict)
    allowed_arguments: dict[str, Any] = Field(default_factory=dict)
    permitted_dependencies: tuple[str, ...] = ()
    permitted_dependency_types: tuple[str, ...] = ()
    result_types: tuple[str, ...] = ()


class ExecutionPlan(PlanVersionedModel):
    plan_id: PlanId
    session_id: SessionId
    request_revision_id: RevisionId
    bound_request_id: BoundRequestId
    canonical_request_hash: str
    plan_hash: str
    capability_version: str
    mode: ExecutionMode
    objective: str | None = None
    fixed_inputs: dict[str, Any] = Field(default_factory=dict)
    catalogue_version: str
    engine_version: str
    country_package_version: str
    dataset_identifier: str
    assumptions: tuple[str, ...] = ()
    allowed_operations: tuple[str, ...] = ()
    operation_constraints: tuple[OperationConstraint, ...] = ()
    required_result_types: tuple[str, ...] = ()
    max_model_iterations: int = Field(default=0, ge=0)
    max_operation_calls: int = Field(default=0, ge=0)
    steps: tuple[PlanStep, ...] = ()
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class OperationExecutionMetadata(FrozenModel):
    step_id: str
    operation: str
    status: Literal["completed", "failed", "cancelled"]
    duration_ms: int = Field(ge=0)
    result_kind: str | None = None
    error_code: str | None = None


class ExecutionAttemptStatus(StrEnum):
    CLAIMED = "claimed"
    RUNNING = "running"
    CANCELLATION_REQUESTED = "cancellation_requested"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"

    @property
    def is_active(self) -> bool:
        return self in {
            ExecutionAttemptStatus.CLAIMED,
            ExecutionAttemptStatus.RUNNING,
            ExecutionAttemptStatus.CANCELLATION_REQUESTED,
        }


class ExecutionAttempt(WorkflowVersionedModel):
    execution_id: ExecutionId
    session_id: SessionId
    request_revision_id: RevisionId
    bound_request_id: BoundRequestId
    plan_id: PlanId
    plan_hash: str
    token_hash: str
    status: ExecutionAttemptStatus = ExecutionAttemptStatus.CLAIMED
    worker_id: str
    catalogue_version: str
    engine_version: str
    country_package_version: str
    dataset_identifier: str
    operations: tuple[OperationExecutionMetadata, ...] = ()
    error_code: str | None = None
    claimed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    heartbeat_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    lease_expires_at: datetime
    completed_at: datetime | None = None


class ExecutionCompletion(FrozenModel):
    execution_id: ExecutionId
    status: Literal["completed", "failed", "cancelled", "superseded", "expired"]
    operations: tuple[OperationExecutionMetadata, ...] = ()
    error_code: str | None = None


class ResultEnvelope(FrozenModel):
    execution_id: ExecutionId
    source_step_id: str
    result_id: ResultId
    result_type: str
    value: Any
    public_summary: dict[str, Any] = Field(default_factory=dict)


class PersistedExecutionMetadata(WorkflowVersionedModel):
    execution_id: str
    session_id: str
    plan_id: str
    plan_hash: str
    status: Literal["completed", "failed", "cancelled"]
    catalogue_version: str
    engine_version: str
    country_package_version: str
    dataset_identifier: str
    operations: tuple[OperationExecutionMetadata, ...] = ()
    error_code: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Fact(FrozenModel):
    fact_id: str
    raw_value: int | float
    unit: str
    display_value: str
    label: str
    source_step_id: str
    caveats: tuple[str, ...] = ()


class FactRegister(FrozenModel):
    facts: tuple[Fact, ...] = ()

    def by_id(self) -> dict[str, Fact]:
        return {fact.fact_id: fact for fact in self.facts}


class ResponseArtifact(FrozenModel):
    kind: Literal["chart"] = "chart"
    artifact_id: str
    content: str


class ExecutionRecord(FrozenModel):
    execution_id: str
    plan_id: str
    operation_summaries: tuple[dict[str, Any], ...] = ()
    fact_register: FactRegister = Field(default_factory=FactRegister)
    caveats: tuple[str, ...] = ()
    response_artifacts: tuple[ResponseArtifact, ...] = ()


class ModelUsageEntry(WorkflowVersionedModel):
    usage_entry_id: str
    session_id: SessionId
    turn_id: TurnId
    operation: Literal[
        "interpretation",
        "reform_target_selection",
        "exploratory_execution",
        "narration",
    ]
    model: str
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_creation_input_tokens: int = Field(default=0, ge=0)
    cache_read_input_tokens: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class BillingIntentStatus(StrEnum):
    PENDING = "pending"
    RECORDED = "recorded"
    FAILED = "failed"


class BillingChargeInput(FrozenModel):
    usage_entry_id: str
    operation: str
    model: str
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_creation_input_tokens: int = Field(default=0, ge=0)
    cache_read_input_tokens: int = Field(default=0, ge=0)
    cost_gbp: float = Field(ge=0)


class BillingIntent(WorkflowVersionedModel):
    billing_intent_id: str
    session_id: SessionId
    turn_id: TurnId
    user_id: str | None = None
    status: BillingIntentStatus = BillingIntentStatus.PENDING
    usage_entry_ids: tuple[str, ...] = ()
    charge_inputs: tuple[BillingChargeInput, ...] = ()
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TurnReceiptStatus(StrEnum):
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    CONFLICT = "conflict"


class CompletedTurnOutcome(FrozenModel):
    kind: Literal["completed"] = "completed"
    content: str
    route: str
    model: str | None = None
    duplicate: bool = False
    response_artifacts: tuple[ResponseArtifact, ...] = ()


class ClarificationTurnOutcome(FrozenModel):
    kind: Literal["clarification"] = "clarification"
    content: str
    question_id: ClarificationId
    reason_code: str
    model: str | None = None
    duplicate: bool = False


class UnsupportedTurnOutcome(FrozenModel):
    kind: Literal["unsupported"] = "unsupported"
    content: str
    reason_code: str
    model: str | None = None
    duplicate: bool = False


class FailedTurnOutcome(FrozenModel):
    kind: Literal["failed"] = "failed"
    content: str
    error_code: str
    retryable: bool = False
    billable: bool = False
    model: str | None = None
    duplicate: bool = False


class CancelledTurnOutcome(FrozenModel):
    kind: Literal["cancelled"] = "cancelled"
    content: str
    request_revision_id: RevisionId | None = None
    model: str | None = None
    duplicate: bool = False


class ConflictTurnOutcome(FrozenModel):
    kind: Literal["conflict"] = "conflict"
    content: str
    retryable: bool = True
    duplicate: bool = False


class StillProcessingTurnOutcome(FrozenModel):
    kind: Literal["still_processing"] = "still_processing"
    content: str


TurnOutcome = Annotated[
    CompletedTurnOutcome
    | ClarificationTurnOutcome
    | UnsupportedTurnOutcome
    | FailedTurnOutcome
    | CancelledTurnOutcome
    | ConflictTurnOutcome
    | StillProcessingTurnOutcome,
    Field(discriminator="kind"),
]
TURN_OUTCOME_ADAPTER = TypeAdapter(TurnOutcome)


class TurnReceipt(WorkflowVersionedModel):
    session_id: SessionId
    turn_id: TurnId
    request_hash: str
    state_version: int
    status: TurnReceiptStatus
    outcome_category: str | None = None
    response_content: str | None = None
    response_metadata: dict[str, Any] = Field(default_factory=dict)
    usage_id: str | None = None
    response_checksum: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PlanStatusChange(FrozenModel):
    kind: Literal["plan_status"] = "plan_status"
    plan_id: PlanId
    expected_status: PlanStatus | None = None
    next_status: PlanStatus


class ExecutionStatusChange(FrozenModel):
    kind: Literal["execution_status"] = "execution_status"
    execution_id: ExecutionId
    expected_status: ExecutionAttemptStatus | None = None
    expected_lease_expires_at: datetime | None = None
    next_status: ExecutionAttemptStatus


TransitionStatusChange = Annotated[
    PlanStatusChange | ExecutionStatusChange,
    Field(discriminator="kind"),
]


class FinalizationIntent(StrEnum):
    COMMIT_TRANSITION = "commit_transition"
    RECEIPT_ONLY = "receipt_only"


class WorkflowTransition(FrozenModel):
    expected_state_version: int = Field(ge=0)
    current_phase: WorkflowPhase
    next_state: AnalysisSessionState
    finalization_intent: FinalizationIntent = FinalizationIntent.COMMIT_TRANSITION
    revisions: tuple[SemanticRequestRevision, ...] = ()
    bound_requests: tuple[BoundRequest, ...] = ()
    clarifications: tuple[PendingClarification, ...] = ()
    clarification_resolutions: tuple[ClarificationResolution, ...] = ()
    plans: tuple[ExecutionPlan, ...] = ()
    execution_attempts: tuple[ExecutionAttempt, ...] = ()
    execution_completions: tuple[ExecutionCompletion, ...] = ()
    turn_receipts: tuple[TurnReceipt, ...] = ()
    usage_entries: tuple[ModelUsageEntry, ...] = ()
    billing_intents: tuple[BillingIntent, ...] = ()
    status_changes: tuple[TransitionStatusChange, ...] = ()
