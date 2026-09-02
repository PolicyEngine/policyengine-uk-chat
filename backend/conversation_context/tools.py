"""Private typed operations for model-assisted fact extraction and reduction."""

from __future__ import annotations

from enum import Enum
import json
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from config import (
    DEFAULT_COMPLEX_MODEL,
    DEFAULT_FAST_MODEL,
    DEFAULT_TEMPERATURE,
    get_async_client,
)
from conversation_context.change_pipeline import (
    ContextChangeApplier,
    ContextChangeProposal,
    ContextChangeValidator,
    ContextValidationOutcome,
    ContextValidationIssue,
    SemanticClaimReview,
    ValidateContextChangeInput,
)
from conversation_context.models import (
    ContextPatch,
    ContextReduction,
    ConversationContext,
    FactClaim,
)
from conversation_context.projection import ContextProjection
from conversation_context.reducer import ContextReducer
from conversation_context.registry import FactDefinition
from tools.contracts import CallerType, Tool, ToolCallContext, ToolSpec, Visibility


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ContextConversationExcerpt(StrictModel):
    role: str
    content: str


class ContextModelUsage(StrictModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    def plus(self, other: "ContextModelUsage") -> "ContextModelUsage":
        return ContextModelUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_creation_input_tokens=(
                self.cache_creation_input_tokens
                + other.cache_creation_input_tokens
            ),
            cache_read_input_tokens=(
                self.cache_read_input_tokens + other.cache_read_input_tokens
            ),
        )


class ProposeContextChangeInput(StrictModel):
    current_message: str
    conversation: tuple[ContextConversationExcerpt, ...]
    context: ContextProjection
    fact_definitions: tuple[FactDefinition, ...]
    previous_proposal: ContextChangeProposal | None = None
    repair_issues: tuple[ContextValidationIssue, ...] = ()


class ContextProposalStatus(str, Enum):
    READY = "ready"
    NEEDS_CLARIFICATION = "needs_clarification"


class ProposeContextChangeOutput(ContextChangeProposal):
    status: ContextProposalStatus = ContextProposalStatus.READY
    issues: tuple[ContextValidationIssue, ...] = ()
    provider_attempts: int = Field(default=1, ge=1, le=2)
    usage: ContextModelUsage = Field(default_factory=ContextModelUsage)


class ContextInterpreter(Protocol):
    async def propose(
        self,
        request: "ProposeContextChangeInput",
    ) -> ProposeContextChangeOutput: ...


class ContextSemanticReview(StrictModel):
    reviews: tuple[SemanticClaimReview, ...] = ()


class ContextSemanticReviewOutput(ContextSemanticReview):
    usage: ContextModelUsage = Field(default_factory=ContextModelUsage)


class ContextProposalReviewer(Protocol):
    async def review(
        self,
        request: ValidateContextChangeInput,
    ) -> ContextSemanticReviewOutput: ...


class AnthropicContextProposalReviewer:
    """Check whether proposed claim semantics match the current user message."""

    async def review(
        self,
        request: ValidateContextChangeInput,
    ) -> ContextSemanticReviewOutput:
        client = get_async_client()  # type: ignore[no-untyped-call]
        tool = {
            "name": "submit_context_semantic_review",
            "description": (
                "Return one semantic-fidelity verdict for every proposed claim."
            ),
            "input_schema": ContextSemanticReview.model_json_schema(),
        }
        system = (
            "Independently review each fact_claim in proposal.changes. Ignore "
            "pending_resolution_response items; deterministic validation checks those "
            "against the referenced retained record. "
            "Use evidence as the exact current user message and the supplied known "
            "entity identities and active scenario scope only to resolve subjects and "
            "conversational references. You do not receive retained fact values. Evaluate "
            "whether each claim's definition concept, grammatical subject, "
            "subject_references, relationship, and value mean what the current message "
            "says. Typed "
            "conversation context represents the active scenario being discussed, not "
            "only the user's real-world biography. Treat facts inside a hypothetical or "
            "counterfactual request as proposed facts for that calculation scenario; do "
            "not reject them merely because the user said 'what if' or used another "
            "conditional construction. A direct claim is valid only when the message "
            "assigns that value to exactly that one "
            "subject. A total, comparison, or other relationship over several known "
            "entities must remain one relational claim over every denoted entity; do not "
            "accept a model-calculated allocation or a direct assignment to one member. "
            "A relational claim is intentionally unresolved: accept it without a "
            "per-entity allocation, and accept a registered per-entity definition_key as "
            "the concept to resolve over all referenced subjects. Never reject a sum "
            "claim merely because its distribution is not supplied. Explicit per-entity "
            "amounts remain separate direct claims. Accept entity and membership facts "
            "that are a faithful typed representation of the scenario stated in the "
            "message. Report only a clear semantic contradiction, not a possible "
            "alternative interpretation. Do not assess "
            "registration, data types, periods, correction flags, arithmetic, capability "
            "requirements, or persistence; deterministic validation handles those. Return "
            "exactly one review for every proposal claim and copy that claim's exact "
            "opaque claim_id into the review. Set "
            "supported=true when the claim is semantically faithful and supported=false "
            "only for a clear mismatch. The reason must agree with the boolean: if the "
            "reason says a claim is valid, faithful, or accurate, supported must be true. "
            "Include the exact supporting text as evidence."
        )
        response = await client.messages.create(
            model=DEFAULT_FAST_MODEL,
            max_tokens=900,
            temperature=DEFAULT_TEMPERATURE,
            system=system,
            messages=[
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "current_message": request.evidence,
                            "known_entities": [
                                entity.model_dump(mode="json")
                                for entity in request.context.entities
                            ],
                            "active_scope_id": request.context.focus.scope_id,
                            "proposal": request.proposal.model_dump(mode="json"),
                        },
                        ensure_ascii=False,
                    ),
                }
            ],
            tools=[tool],
            tool_choice={
                "type": "tool",
                "name": "submit_context_semantic_review",
            },
        )
        block = next(
            (
                item
                for item in response.content
                if getattr(item, "type", None) == "tool_use"
                and getattr(item, "name", None)
                == "submit_context_semantic_review"
            ),
            None,
        )
        usage = AnthropicContextInterpreter._usage(response)
        if block is None:
            return ContextSemanticReviewOutput(
                usage=usage,
            )
        try:
            review = ContextSemanticReview.model_validate(block.input)
        except ValidationError:
            return ContextSemanticReviewOutput(
                usage=usage,
            )
        return ContextSemanticReviewOutput(reviews=review.reviews, usage=usage)


class AnthropicContextInterpreter:
    """Propose candidate facts without deciding capability requirements."""

    async def propose(
        self,
        request: "ProposeContextChangeInput",
    ) -> ProposeContextChangeOutput:
        client = get_async_client()  # type: ignore[no-untyped-call]
        tool = {
            "name": "submit_context_change",
            "description": (
                "Return candidate entities and one ordered list of typed context changes."
            ),
            "input_schema": ContextChangeProposal.model_json_schema(),
        }
        system = (
            "Interpret only facts explicitly stated, corrected, or denied in the exact "
            "current user message into one declarative context proposal. Return every "
            "new assertion and pending-resolution response in the single ordered changes "
            "list. Use kind=fact_claim for a new assertion and "
            "kind=pending_resolution_response for an action on one exact retained "
            "pending resolution. Use "
            "conversation excerpts only to resolve pronouns, aliases, and which pending "
            "requirements a short answer addresses; do not copy old transcript claims "
            "that are absent from typed context. Return every supported assertion exactly "
            "once as a fact_claim. Claims are declarative; never return a context patch, set-fact "
            "operation, or a separate unresolved-claim collection. Use only supplied fact "
            "definitions, entity identifiers, and scope identifiers. Declare a newly "
            "mentioned person or policy scenario in candidate_entities and reference that "
            "same candidate from claims. A direct claim has exactly one grammatical "
            "subject. A sum claim has every person denoted by its grammatical subject and "
            "at least two distinct subject references. A statement about several people "
            "is a relationship over those people, not a direct fact about one person and "
            "not an invented household-level version of a person fact. Mark correction "
            "only when the current message clearly corrects an active value. Use "
            "definition_key for every non-monetary claim; it must equal one exact key "
            "from fact_definitions, because concept alone does not select a definition. "
            "When a short answer satisfies a pending requirement, copy that requirement's "
            "fact_key into definition_key, its subject_entity_id into subject_references, "
            "and its scope_id into scope_id. "
            "Set correction true when the current message explicitly tells you to use, "
            "set, change, correct, or keep a value and typed context already contains a "
            "different value for that same fact, subject, and scope. The flag describes "
            "fact replacement; it does not require the user to say that an earlier value "
            "was wrong. "
            "explicit_absence only when the user says a registered optional value does not "
            "apply; zero is a present numeric value. Leave an unstated monetary period null. "
            "Validation may inherit it only from one compatible active fact "
            "or pending fact-resolution constraint; otherwise the applicable capability "
            "decides from the exact current message whether to apply a documented "
            "non-persistent default or ask the user. "
            "Do not infer an income source or frequency, apply a default, decide what "
            "a capability requires, ask a question, select a capability, allocate a total, "
            "or calculate anything. If the current message clearly accepts or rejects one "
            "pending fact-resolution proposal whose status is awaiting_confirmation, put "
            "one pending_resolution_response in changes with its exact proposal_id, "
            "action=accept or action=reject, the shortest exact current-message evidence, "
            "and no updates. Do not reconstruct its assignments. When a short answer "
            "supplies one or more fields requested by a needs_clarification resolution, "
            "return action=supply and only schema-addressed updates supported by the exact "
            "current message. For example, an annual-period answer supplies path "
            "[\"value\",\"period\"] and value \"annual\". Do not copy its retained "
            "amount, subjects, relationship, mapping, or source evidence into the current "
            "proposal. An explicit user-supplied per-person breakdown remains ordinary "
            "direct fact_claim items; do not also emit a supply response for that "
            "breakdown. Supply updates may address only fields of the retained source "
            "fact claim, such as [\"value\",\"period\"] or "
            "[\"value\",\"amount\"]. Never update resolver-owned terms, assignments, "
            "known values, prompts, or mapping metadata. You cannot create a pending "
            "fact-resolution proposal or replace "
            "pending capability questions; those are server-owned. Treat calculation "
            "metric categories explicitly named in the current request as an "
            "analysis.requested_outputs fact on the relevant household or policy "
            "scenario; do not invent outputs that were not requested. Generic scope "
            "phrases such as 'societal impact', 'society-wide impact', 'population "
            "impact', 'impact on all of society', and 'overall impact' request the "
            "default population analysis; they are not metric names and must not create "
            "an analysis.requested_outputs fact by themselves. Named metrics include "
            "poverty, inequality, Gini, programme statistics, caseload, budgetary "
            "impact, winners and losers, and income-decile distribution. A short answer such "
            "as 'neither' may satisfy every compatible pending requirement. Set each "
            "claim's evidence to the shortest exact contiguous quote from current_message "
            "that supports it; for a one-token answer such as '27', use exactly that "
            "token. A pending_resolution_response may identify only an exact proposal_id listed in "
            "pending_fact_resolutions, never a pending capability identifier. Set "
            "expected_revision to the supplied context revision. Return no operations "
            "and no claims when the message establishes no contextual fact."
        )
        issues = request.repair_issues
        correction = self._repair_instruction(issues) if issues else ""
        max_attempts = 1 if issues else 2
        usage_total = ContextModelUsage()
        for attempt in range(max_attempts):
            response = await client.messages.create(
                model=(DEFAULT_COMPLEX_MODEL if issues else DEFAULT_FAST_MODEL),
                max_tokens=1800,
                temperature=DEFAULT_TEMPERATURE,
                system=system,
                messages=[
                    {
                        "role": "user",
                        "content": request.model_dump_json() + correction,
                    }
                ],
                tools=[tool],
                tool_choice={"type": "tool", "name": "submit_context_change"},
            )
            usage_total = usage_total.plus(self._usage(response))
            block = next(
                (
                    item
                    for item in response.content
                    if getattr(item, "type", None) == "tool_use"
                    and getattr(item, "name", None) == "submit_context_change"
                ),
                None,
            )
            if block is None:
                issues = (
                    ContextValidationIssue(
                        code="missing_structured_context_output",
                        path=("submit_context_change",),
                        message=(
                            "The context interpreter did not return the required "
                            "structured tool output."
                        ),
                        evidence=request.current_message,
                    ),
                )
                correction = self._repair_instruction(issues)
                continue
            try:
                submission = ContextChangeProposal.model_validate(block.input)
            except ValidationError as exc:
                issues = tuple(
                    ContextValidationIssue(
                        code="invalid_context_submission",
                        path=tuple(str(item) for item in error["loc"]),
                        message=error["msg"],
                        evidence=request.current_message,
                    )
                    for error in exc.errors(include_url=False, include_input=False)
                )
                correction = self._repair_instruction(issues)
                continue
            return ProposeContextChangeOutput(
                **submission.model_dump(mode="python"),
                provider_attempts=attempt + 1,
                usage=usage_total,
            )
        return ProposeContextChangeOutput(
            status=ContextProposalStatus.NEEDS_CLARIFICATION,
            expected_revision=request.context.revision,
            issues=issues,
            provider_attempts=max_attempts,
            usage=usage_total,
        )

    @staticmethod
    def _repair_instruction(issues: tuple[ContextValidationIssue, ...]) -> str:
        payload = [issue.model_dump(mode="json") for issue in issues]
        return (
            "\n\nThe previous context-change proposal did not validate. Correct only "
            "the previous_proposal using these machine-readable issues. Preserve its "
            "valid changes and revise only invalid fields. Do not repeat a value rejected at the "
            "cited path. In particular, an "
            "unmapped_fact_claim requires definition_key to be set to one exact supplied "
            "fact-definition key; concept does not satisfy it. An uncited evidence issue "
            "requires a shortest exact quote from current_message. Preserve valid fields, "
            "and correct a pending-resolution response only from its referenced record and "
            "the exact current-message evidence. Never copy retained fields into a supply "
            "response. If ordinary direct fact_claim items already express an explicit "
            "per-subject breakdown, remove a redundant supply response entirely. A supply "
            "response never updates resolver-owned terms, assignments, known values, "
            "prompts, or mapping metadata. "
            "For semantic_claim_mismatch, revise the cited claim to match the review. If a "
            "direct claim was rejected because the message relates several entities, keep "
            "the exact stated value as one relational claim over all denoted subjects; do "
            "not calculate an allocation or drop a current-message monetary value. "
            "A context_operation_conflicted issue means a generated fact would replace a "
            "different active value without permission: set that claim's correction field "
            "true only when the current message explicitly directs the new or retained "
            "value; otherwise remove the unsupported claim. "
            "use supplied stable entity references, and do not ask the user a question, "
            "invent a default, or create a proposal_response unless its exact identifier "
            "appears in pending_fact_resolutions:\n"
            + json.dumps(payload, ensure_ascii=False)
        )

    @staticmethod
    def _usage(response: object) -> ContextModelUsage:
        usage = getattr(response, "usage", None)
        return ContextModelUsage(
            input_tokens=getattr(usage, "input_tokens", 0),
            output_tokens=getattr(usage, "output_tokens", 0),
            cache_creation_input_tokens=getattr(
                usage,
                "cache_creation_input_tokens",
                0,
            ),
            cache_read_input_tokens=getattr(
                usage,
                "cache_read_input_tokens",
                0,
            ),
        )


class ProposeContextChangeTool(
    Tool["ProposeContextChangeInput", ProposeContextChangeOutput]
):
    spec = ToolSpec(
        identifier="propose_context_change",
        version="1",
        description=(
            "Interpret candidate entities and declarative fact claims from the exact "
            "current message."
        ),
        visibility=Visibility.PRIVATE,
        allowed_callers=frozenset({CallerType.RUNTIME}),
        input_model=ProposeContextChangeInput,
        output_model=ProposeContextChangeOutput,
    )

    def __init__(self, interpreter: ContextInterpreter) -> None:
        self._interpreter = interpreter

    async def run(
        self,
        tool_input: "ProposeContextChangeInput",
        context: ToolCallContext,
    ) -> ProposeContextChangeOutput:
        result = await self._interpreter.propose(tool_input)
        context.record_model_usage(**result.usage.model_dump())
        return result


class ReduceContextPatchInput(StrictModel):
    context: ConversationContext
    patch: ContextPatch
    turn_id: str
    evidence: str


class ReduceContextPatchTool(Tool[ReduceContextPatchInput, ContextReduction]):
    spec = ToolSpec(
        identifier="reduce_context_patch",
        version="1",
        description=(
            "Validate candidate registered facts and produce the next context revision."
        ),
        visibility=Visibility.PRIVATE,
        allowed_callers=frozenset({CallerType.RUNTIME}),
        input_model=ReduceContextPatchInput,
        output_model=ContextReduction,
    )

    def __init__(self, reducer: ContextReducer) -> None:
        self._reducer = reducer

    async def run(
        self,
        tool_input: ReduceContextPatchInput,
        context: ToolCallContext,
    ) -> ContextReduction:
        del context
        return self._reducer.reduce(
            tool_input.context,
            tool_input.patch,
            turn_id=tool_input.turn_id,
            evidence=tool_input.evidence,
        )


class ValidateContextChangeTool(
    Tool[ValidateContextChangeInput, ContextValidationOutcome]
):
    spec = ToolSpec(
        identifier="validate_context_change",
        version="1",
        description=(
            "Validate one complete current-message context update without partial "
            "persistence."
        ),
        visibility=Visibility.PRIVATE,
        allowed_callers=frozenset({CallerType.RUNTIME}),
        input_model=ValidateContextChangeInput,
        output_model=ContextValidationOutcome,
    )

    def __init__(
        self,
        validator: ContextChangeValidator,
        reviewer: ContextProposalReviewer | None = None,
    ) -> None:
        self._validator = validator
        self._reviewer = reviewer

    async def run(
        self,
        tool_input: ValidateContextChangeInput,
        context: ToolCallContext,
    ) -> ContextValidationOutcome:
        semantic_issues: tuple[ContextValidationIssue, ...] = ()
        semantic_reviews: tuple[SemanticClaimReview, ...] = ()
        if (
            self._reviewer is not None
            and not tool_input.claims_resolved
            and tool_input.proposal.claims
        ):
            review = await self._reviewer.review(tool_input)
            context.record_model_usage(**review.usage.model_dump())
            semantic_reviews = review.reviews
            claim_indexes = {
                change.claim_id: index
                for index, change in enumerate(tool_input.proposal.changes)
                if isinstance(change, FactClaim)
            }
            expected_claim_ids = set(claim_indexes)
            reviewed_claim_ids = {item.claim_id for item in semantic_reviews}
            complete_review = (
                len(claim_indexes) == len(tool_input.proposal.claims)
                and len(semantic_reviews) == len(reviewed_claim_ids)
                and reviewed_claim_ids == expected_claim_ids
            )
            semantic_issues = tuple(
                ContextValidationIssue(
                    code="semantic_claim_mismatch",
                    message=item.reason,
                    path=(
                        "proposal",
                        "changes",
                        str(claim_indexes[item.claim_id]),
                    ),
                    claim_index=claim_indexes[item.claim_id],
                    evidence=item.evidence,
                )
                for item in semantic_reviews
                if not item.supported and item.claim_id in claim_indexes
            )
            if not complete_review:
                semantic_issues = (
                    ContextValidationIssue(
                        code="semantic_review_failed",
                        message=(
                            "The semantic review did not return exactly one verdict for "
                            "every proposed claim."
                        ),
                        path=("proposal", "claims"),
                        evidence=tool_input.evidence,
                    ),
                )
        outcome = self._validator.validate(
            tool_input,
            semantic_issues=semantic_issues,
        )
        return outcome.model_copy(
            update={"semantic_reviews": semantic_reviews}
        )


class ApplyContextChangeInput(StrictModel):
    outcome: ContextValidationOutcome


class ApplyContextChangeTool(Tool[ApplyContextChangeInput, ConversationContext]):
    spec = ToolSpec(
        identifier="apply_context_change",
        version="1",
        description="Persist one fully validated context change atomically.",
        visibility=Visibility.PRIVATE,
        allowed_callers=frozenset({CallerType.RUNTIME}),
        input_model=ApplyContextChangeInput,
        output_model=ConversationContext,
    )

    def __init__(self, applier: ContextChangeApplier) -> None:
        self._applier = applier

    async def run(
        self,
        tool_input: ApplyContextChangeInput,
        context: ToolCallContext,
    ) -> ConversationContext:
        del context
        return self._applier.apply(tool_input.outcome)
