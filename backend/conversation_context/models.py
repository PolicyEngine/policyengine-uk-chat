"""Immutable models for stable conversational entities and registered facts."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Annotated, Literal, TypeAlias
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


def _now() -> datetime:
    return datetime.now(timezone.utc)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EntityKind(str, Enum):
    PERSON = "person"
    HOUSEHOLD = "household"
    POLICY_SCENARIO = "policy_scenario"


class ScopeKind(str, Enum):
    HOUSEHOLD = "household"
    POLICY = "policy"
    CONVERSATION = "conversation"


class MoneyPeriod(str, Enum):
    ANNUAL = "annual"
    MONTHLY = "monthly"
    FOUR_WEEKLY = "four_weekly"
    WEEKLY = "weekly"


class FactClaimRelationship(str, Enum):
    DIRECT = "direct"
    SUM = "sum"


class FactResolutionStatus(str, Enum):
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    NEEDS_CLARIFICATION = "needs_clarification"


class PendingResolutionAction(str, Enum):
    ACCEPT = "accept"
    REJECT = "reject"
    SUPPLY = "supply"


class PendingQuestionStatus(str, Enum):
    AWAITING_ANSWER = "awaiting_answer"
    ANSWER_RECEIVED = "answer_received"


class ContextEntity(StrictModel):
    entity_id: str
    kind: EntityKind
    aliases: tuple[str, ...] = ()
    relationship_to_user: str | None = None
    created_turn_id: str | None = None


class ContextScope(StrictModel):
    scope_id: str
    kind: ScopeKind
    subject_entity_ids: tuple[str, ...] = ()
    active: bool = True


class BooleanFactValue(StrictModel):
    kind: Literal["boolean"] = "boolean"
    value: bool


class IntegerFactValue(StrictModel):
    kind: Literal["integer"] = "integer"
    value: int


class MoneyFactValue(StrictModel):
    kind: Literal["money"] = "money"
    amount: Decimal
    period: MoneyPeriod
    currency: Literal["GBP"] = "GBP"


class ClaimedMoneyValue(StrictModel):
    """A cited monetary value that is not yet an accepted engine-backed fact."""

    kind: Literal["money"] = "money"
    amount: Decimal
    period: MoneyPeriod | None = None
    currency: Literal["GBP"] = "GBP"


class TextFactValue(StrictModel):
    kind: Literal["text"] = "text"
    value: str


class TextSetFactValue(StrictModel):
    kind: Literal["text_set"] = "text_set"
    values: tuple[str, ...]


class EntityReferenceFactValue(StrictModel):
    kind: Literal["entity_reference"] = "entity_reference"
    entity_id: str


class EntityReferencesFactValue(StrictModel):
    kind: Literal["entity_references"] = "entity_references"
    entity_ids: tuple[str, ...]


FactValue: TypeAlias = Annotated[
    BooleanFactValue
    | IntegerFactValue
    | MoneyFactValue
    | TextFactValue
    | TextSetFactValue
    | EntityReferenceFactValue
    | EntityReferencesFactValue,
    Field(discriminator="kind"),
]


ClaimValue: TypeAlias = Annotated[
    BooleanFactValue
    | IntegerFactValue
    | ClaimedMoneyValue
    | TextFactValue
    | TextSetFactValue
    | EntityReferenceFactValue
    | EntityReferencesFactValue,
    Field(discriminator="kind"),
]


class PresentAssertion(StrictModel):
    kind: Literal["present"] = "present"
    value: FactValue


class ExplicitAbsenceAssertion(StrictModel):
    kind: Literal["explicit_absence"] = "explicit_absence"


FactAssertion: TypeAlias = Annotated[
    PresentAssertion | ExplicitAbsenceAssertion,
    Field(discriminator="kind"),
]


class FactProvenance(StrictModel):
    turn_id: str
    source: Literal["user", "artifact", "system"] = "user"
    evidence: str | None = None


class ContextFact(StrictModel):
    fact_id: str = Field(default_factory=lambda: uuid4().hex)
    definition_key: str
    definition_version: str = "1"
    subject_entity_id: str
    scope_id: str
    assertion: FactAssertion
    provenance: FactProvenance
    introduced_revision: int
    supersedes_fact_id: str | None = None
    created_at: datetime = Field(default_factory=_now)


class FactRequirement(StrictModel):
    requirement_id: str
    fact_key: str
    subject_entity_id: str | None = None
    subject_kind: EntityKind | None = None
    scope_id: str
    expected_value_kind: str
    allow_explicit_absence: bool = False
    reason: str


class CapabilityInvocationReference(StrictModel):
    """Typed link from conversational state to one waiting capability call."""

    invocation_id: str
    capability_id: str
    capability_version: str
    context_scope_id: str
    context_revision: int


class FactClaim(StrictModel):
    """One declarative interpretation of a user-stated contextual fact."""

    kind: Literal["fact_claim"] = "fact_claim"
    claim_id: str = Field(default_factory=lambda: uuid4().hex)
    concept: str
    subject_references: tuple[str, ...]
    relationship: FactClaimRelationship = FactClaimRelationship.DIRECT
    value: ClaimValue | None = None
    explicit_absence: bool = False
    definition_key: str | None = None
    definition_version: str = "1"
    scope_id: str | None = None
    correction: bool = False
    evidence: str

    @model_validator(mode="after")
    def validate_claim(self) -> "FactClaim":
        if not self.concept.strip():
            raise ValueError("fact claim concept must not be empty")
        if not self.evidence.strip():
            raise ValueError("fact claim evidence must not be empty")
        if not self.subject_references:
            raise ValueError("fact claim requires at least one subject reference")
        normalized_subjects = {
            reference.strip().casefold()
            for reference in self.subject_references
            if reference.strip()
        }
        if len(normalized_subjects) != len(self.subject_references):
            raise ValueError("fact claim subject references must be non-empty and unique")
        if self.explicit_absence == (self.value is not None):
            raise ValueError(
                "fact claim requires exactly one of value or explicit_absence"
            )
        if self.relationship is FactClaimRelationship.DIRECT:
            if len(self.subject_references) != 1:
                raise ValueError("a direct fact claim requires exactly one subject")
            return self
        if len(self.subject_references) < 2:
            raise ValueError("an additive fact claim requires at least two subjects")
        if self.explicit_absence or not isinstance(self.value, ClaimedMoneyValue):
            raise ValueError("an additive fact claim requires one monetary value")
        return self


class ContextEntityCandidate(StrictModel):
    """A model-interpreted entity declaration without persistence authority."""

    reference: str
    kind: EntityKind
    aliases: tuple[str, ...] = ()
    relationship_to_user: str | None = None


class ContextFocusCandidate(StrictModel):
    """A model-interpreted focus change without persistence authority."""

    scope_id: str | None = None
    entity_references: tuple[str, ...] = ()


class FactClaimFieldUpdate(StrictModel):
    """One current-message value supplied for a field on a retained fact claim."""

    path: tuple[str, ...]
    value: JsonValue
    evidence: str

    @model_validator(mode="after")
    def validate_update(self) -> "FactClaimFieldUpdate":
        if not self.path or any(not part.strip() for part in self.path):
            raise ValueError("fact-claim update path must contain non-empty fields")
        if not self.evidence.strip():
            raise ValueError("fact-claim update evidence must not be empty")
        return self


class PendingFactResolutionResponse(StrictModel):
    """A current-message action on one retained server-authored resolution."""

    kind: Literal["pending_resolution_response"] = "pending_resolution_response"
    response_id: str = Field(default_factory=lambda: uuid4().hex)
    proposal_id: str
    action: PendingResolutionAction
    updates: tuple[FactClaimFieldUpdate, ...] = ()
    evidence: str

    @model_validator(mode="after")
    def validate_response(self) -> "PendingFactResolutionResponse":
        if not self.evidence.strip():
            raise ValueError("pending-resolution response evidence must not be empty")
        if self.action is PendingResolutionAction.SUPPLY:
            if not self.updates:
                raise ValueError("a supply response requires at least one field update")
        elif self.updates:
            raise ValueError("accept and reject responses cannot contain field updates")
        return self


ProposedContextChange: TypeAlias = Annotated[
    FactClaim | PendingFactResolutionResponse,
    Field(discriminator="kind"),
]


class FactResolutionSupplement(StrictModel):
    """Validated current-message fields added to one retained source claim."""

    turn_id: str
    evidence: str
    updates: tuple[FactClaimFieldUpdate, ...]


class FactResolutionTerm(StrictModel):
    variable_name: str
    subject_entity_id: str
    coefficient: Decimal = Decimal("1")
    known_value: Decimal | None = None


class FactResolutionAssignment(StrictModel):
    definition_key: str
    definition_version: str = "1"
    subject_entity_id: str
    scope_id: str
    assertion: PresentAssertion
    correction: bool = False


class PendingFactResolution(StrictModel):
    """A validated variable mapping or calculation awaiting user input."""

    proposal_id: str = Field(default_factory=lambda: uuid4().hex)
    claim_id: str
    source_turn_id: str
    source_claim: FactClaim | None = None
    supplements: tuple[FactResolutionSupplement, ...] = ()
    scope_id: str
    referenced_entity_ids: tuple[str, ...]
    evidence: str
    status: FactResolutionStatus
    prompt: str
    variable_name: str | None = None
    variable_entity: str | None = None
    variable_label: str | None = None
    definition_period: str | None = None
    mapping_confidence: str | None = None
    relationship: FactClaimRelationship
    expected_total: Decimal | None = None
    period: MoneyPeriod | None = None
    terms: tuple[FactResolutionTerm, ...] = ()
    assignments: tuple[FactResolutionAssignment, ...] = ()
    created_revision: int


class PendingQuestion(StrictModel):
    question_id: str = Field(default_factory=lambda: uuid4().hex)
    capability_id: str
    capability_invocation: CapabilityInvocationReference | None = None
    prompt: str
    requirements: tuple[FactRequirement, ...]
    created_turn_id: str
    status: PendingQuestionStatus = PendingQuestionStatus.AWAITING_ANSWER

    @model_validator(mode="after")
    def validate_capability_invocation(self) -> "PendingQuestion":
        reference = self.capability_invocation
        if reference is not None and reference.capability_id != self.capability_id:
            raise ValueError(
                "pending question capability does not match its invocation reference"
            )
        return self


class ConversationFocus(StrictModel):
    scope_id: str | None = None
    entity_ids: tuple[str, ...] = ()
    artifact_ids: tuple[str, ...] = ()


class EnsureEntityOperation(StrictModel):
    operation: Literal["ensure_entity"] = "ensure_entity"
    reference: str
    kind: EntityKind
    aliases: tuple[str, ...] = ()
    relationship_to_user: str | None = None


class SetFactOperation(StrictModel):
    operation: Literal["set_fact"] = "set_fact"
    definition_key: str
    definition_version: str = "1"
    subject_reference: str
    scope_id: str
    assertion: FactAssertion
    correction: bool = False


class SetFocusOperation(StrictModel):
    operation: Literal["set_focus"] = "set_focus"
    scope_id: str | None = None
    entity_references: tuple[str, ...] = ()


class ReplacePendingQuestionsOperation(StrictModel):
    operation: Literal["replace_pending_questions"] = "replace_pending_questions"
    questions: tuple[PendingQuestion, ...] = ()


class AddPendingFactResolutionOperation(StrictModel):
    operation: Literal["add_pending_fact_resolution"] = "add_pending_fact_resolution"
    proposal: PendingFactResolution


class ConfirmPendingFactResolutionOperation(StrictModel):
    operation: Literal["confirm_pending_fact_resolution"] = (
        "confirm_pending_fact_resolution"
    )
    proposal_id: str
    accepted: bool


ContextOperation: TypeAlias = Annotated[
    EnsureEntityOperation
    | SetFactOperation
    | SetFocusOperation
    | ReplacePendingQuestionsOperation
    | AddPendingFactResolutionOperation
    | ConfirmPendingFactResolutionOperation,
    Field(discriminator="operation"),
]


class ContextPatch(StrictModel):
    expected_revision: int
    operations: tuple[ContextOperation, ...] = ()


class FactDecisionStatus(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CONFLICTED = "conflicted"
    IGNORED = "ignored"
    SUPERSEDED = "superseded"


class FactDecision(StrictModel):
    operation_index: int
    status: FactDecisionStatus
    operation: str
    definition_key: str | None = None
    subject_entity_id: str | None = None
    fact_id: str | None = None
    superseded_fact_id: str | None = None
    reason: str


class ConversationContext(StrictModel):
    schema_version: Literal["1"] = "1"
    conversation_id: str
    revision: int = 0
    entities: tuple[ContextEntity, ...]
    scopes: tuple[ContextScope, ...]
    facts: tuple[ContextFact, ...] = ()
    pending_questions: tuple[PendingQuestion, ...] = ()
    pending_fact_resolutions: tuple[PendingFactResolution, ...] = ()
    focus: ConversationFocus = Field(default_factory=ConversationFocus)
    updated_at: datetime = Field(default_factory=_now)

    @classmethod
    def initial(cls, conversation_id: str) -> "ConversationContext":
        person_id = "person:self"
        household_id = "household:primary"
        scope_id = "scope:primary-household"
        return cls(
            conversation_id=conversation_id,
            entities=(
                ContextEntity(
                    entity_id=person_id,
                    kind=EntityKind.PERSON,
                    aliases=("I", "me", "myself", "the user"),
                    relationship_to_user="self",
                ),
                ContextEntity(
                    entity_id=household_id,
                    kind=EntityKind.HOUSEHOLD,
                    aliases=("my household", "the household"),
                    relationship_to_user="primary_household",
                ),
            ),
            scopes=(
                ContextScope(
                    scope_id=scope_id,
                    kind=ScopeKind.HOUSEHOLD,
                    subject_entity_ids=(household_id, person_id),
                ),
            ),
            focus=ConversationFocus(
                scope_id=scope_id,
                entity_ids=(household_id, person_id),
            ),
        )

    @model_validator(mode="after")
    def validate_references(self) -> "ConversationContext":
        entity_ids = {entity.entity_id for entity in self.entities}
        if len(entity_ids) != len(self.entities):
            raise ValueError("context entity identifiers must be unique")
        scope_ids = {scope.scope_id for scope in self.scopes}
        if len(scope_ids) != len(self.scopes):
            raise ValueError("context scope identifiers must be unique")
        for scope in self.scopes:
            if not set(scope.subject_entity_ids) <= entity_ids:
                raise ValueError("context scope references an unknown entity")
        for fact in self.facts:
            if fact.subject_entity_id not in entity_ids:
                raise ValueError("context fact references an unknown entity")
            if fact.scope_id not in scope_ids:
                raise ValueError("context fact references an unknown scope")
        proposal_ids = {
            proposal.proposal_id for proposal in self.pending_fact_resolutions
        }
        if len(proposal_ids) != len(self.pending_fact_resolutions):
            raise ValueError("pending fact-resolution identifiers must be unique")
        for proposal in self.pending_fact_resolutions:
            if proposal.scope_id not in scope_ids:
                raise ValueError("pending fact resolution references an unknown scope")
            if not set(proposal.referenced_entity_ids) <= entity_ids:
                raise ValueError("pending fact resolution references an unknown entity")
            for assignment in proposal.assignments:
                if assignment.subject_entity_id not in entity_ids:
                    raise ValueError(
                        "fact-resolution assignment references an unknown entity"
                    )
                if assignment.scope_id not in scope_ids:
                    raise ValueError(
                        "fact-resolution assignment references an unknown scope"
                    )
        question_ids = {question.question_id for question in self.pending_questions}
        if len(question_ids) != len(self.pending_questions):
            raise ValueError("pending question identifiers must be unique")
        invocation_ids: set[str] = set()
        for question in self.pending_questions:
            for requirement in question.requirements:
                if requirement.scope_id not in scope_ids:
                    raise ValueError("pending question requirement references an unknown scope")
                if (
                    requirement.subject_entity_id is not None
                    and requirement.subject_entity_id not in entity_ids
                ):
                    raise ValueError(
                        "pending question requirement references an unknown entity"
                    )
            reference = question.capability_invocation
            if reference is None:
                # Version-one contexts written by the initial implementation did
                # not persist this link. ChatTurnService repairs an unambiguous
                # legacy record before supplying context to a new turn.
                continue
            if reference.context_scope_id not in scope_ids:
                raise ValueError("pending capability invocation references an unknown scope")
            if reference.invocation_id in invocation_ids:
                raise ValueError(
                    "a capability invocation cannot own more than one pending question"
                )
            invocation_ids.add(reference.invocation_id)
        return self

    def active_facts(self) -> tuple[ContextFact, ...]:
        superseded = {
            fact.supersedes_fact_id
            for fact in self.facts
            if fact.supersedes_fact_id is not None
        }
        return tuple(fact for fact in self.facts if fact.fact_id not in superseded)

    def active_fact(
        self,
        definition_key: str,
        subject_entity_id: str,
        scope_id: str,
    ) -> ContextFact | None:
        return next(
            (
                fact
                for fact in reversed(self.active_facts())
                if fact.definition_key == definition_key
                and fact.subject_entity_id == subject_entity_id
                and fact.scope_id == scope_id
            ),
            None,
        )


class ContextReduction(StrictModel):
    previous_revision: int
    context: ConversationContext
    decisions: tuple[FactDecision, ...]
