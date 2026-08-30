"""Typed coordination of conversation-context changes and pending questions."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from capabilities.context import CapabilityContext
from capabilities.executor import InvocationExecutor
from conversation_context.change_pipeline import (
    ContextChangeProposal,
    ContextValidationIssue,
    ContextValidationOutcome,
    ContextValidationStatus,
    ValidateContextChangeInput,
)
from conversation_context.models import (
    CapabilityInvocationReference,
    ContextPatch,
    ContextReduction,
    ConversationContext,
    FactRequirement,
    PendingQuestion,
    PendingQuestionStatus,
    ReplacePendingQuestionsOperation,
)
from conversation_context.projection import project_context
from conversation_context.registry import FactDefinitionRegistry
from conversation_context.repository import ConversationContextRepository
from conversation_context.tools import (
    ApplyContextChangeInput,
    ContextConversationExcerpt,
    ContextProposalStatus,
    ProposeContextChangeInput,
    ProposeContextChangeOutput,
    ReduceContextPatchInput,
)
from conversation_context.variable_resolution import (
    ResolveContextChangeInput,
    ResolveContextChangeOutput,
)
from tools.contracts import CallerType


@dataclass(frozen=True)
class ContextChangeTurn:
    """Current evidence supplied to the context-change pipeline."""

    current_message: str
    conversation: tuple[ContextConversationExcerpt, ...]


class ContextChangeCoordinator:
    """Propose, validate, resolve, and apply one context revision."""

    def __init__(
        self,
        *,
        executor: InvocationExecutor,
        fact_registry: FactDefinitionRegistry,
    ) -> None:
        self._executor = executor
        self._fact_registry = fact_registry

    async def process(
        self,
        turn: ContextChangeTurn,
        context: CapabilityContext,
    ) -> ContextValidationOutcome:
        if context.conversation_context is None:
            raise RuntimeError("Conversation context processing is not configured.")
        prior = context.conversation_context
        repair_issues: tuple[ContextValidationIssue, ...] = ()
        previous_proposal: ContextChangeProposal | None = None
        for proposal_attempt in range(2):
            raw_proposal = await self._executor.invoke_tool(
                "propose_context_change",
                ProposeContextChangeInput(
                    current_message=turn.current_message,
                    conversation=turn.conversation,
                    context=project_context(prior),
                    fact_definitions=self._fact_registry.definitions(),
                    previous_proposal=previous_proposal,
                    repair_issues=repair_issues,
                ),
                caller=CallerType.RUNTIME,
                context=context,
            )
            if not isinstance(raw_proposal, ProposeContextChangeOutput):
                raise TypeError("Context interpretation returned an incompatible output.")
            if raw_proposal.status is ContextProposalStatus.NEEDS_CLARIFICATION:
                return self._issue_outcome(prior, raw_proposal.issues)

            proposal = ContextChangeProposal(
                expected_revision=raw_proposal.expected_revision,
                candidate_entities=raw_proposal.candidate_entities,
                changes=raw_proposal.changes,
                focus=raw_proposal.focus,
            )
            validation = await self._validate(
                prior=prior,
                proposal=proposal,
                context=context,
                evidence=turn.current_message,
            )
            if validation.status is ContextValidationStatus.NEEDS_CLARIFICATION:
                if proposal_attempt == 0:
                    previous_proposal = proposal
                    repair_issues = validation.issues
                    continue
                return validation

            validated = validation
            if validation.claims_to_resolve:
                try:
                    raw_resolution = await self._executor.invoke_tool(
                        "resolve_context_change",
                        ResolveContextChangeInput(
                            context=validation.context,
                            proposal=proposal,
                            validation_issues=validation.issues,
                            claims=validation.claims_to_resolve,
                            turn_id=context.turn_id,
                            evidence=turn.current_message,
                        ),
                        caller=CallerType.RUNTIME,
                        context=context,
                    )
                except (TypeError, ValueError, RuntimeError):
                    issues = (
                        ContextValidationIssue(
                            code="fact_resolution_failed",
                            path=("claims",),
                            message=(
                                "The current-message fact claim could not be "
                                "validated against an authoritative variable."
                            ),
                            evidence=turn.current_message,
                        ),
                    )
                    if proposal_attempt == 0:
                        previous_proposal = proposal
                        repair_issues = issues
                        continue
                    return self._issue_outcome(prior, issues)
                if not isinstance(raw_resolution, ResolveContextChangeOutput):
                    raise TypeError(
                        "Fact-claim resolution returned an incompatible output."
                    )
                validated = await self._validate(
                    prior=prior,
                    proposal=proposal,
                    context=context,
                    evidence=turn.current_message,
                    resolution_patch=raw_resolution.patch,
                    claims_resolved=True,
                )
                if validated.status is ContextValidationStatus.NEEDS_CLARIFICATION:
                    if proposal_attempt == 0:
                        previous_proposal = proposal
                        repair_issues = validated.issues
                        continue
                    return validated

            if validated.committable:
                applied = await self._executor.invoke_tool(
                    "apply_context_change",
                    ApplyContextChangeInput(outcome=validated),
                    caller=CallerType.RUNTIME,
                    context=context,
                )
                if not isinstance(applied, ConversationContext):
                    raise TypeError("Context application returned an incompatible output.")
                validated = validated.model_copy(update={"context": applied})
            return validated
        return self._issue_outcome(prior, repair_issues)

    async def _validate(
        self,
        *,
        prior: ConversationContext,
        proposal: ContextChangeProposal,
        context: CapabilityContext,
        evidence: str,
        resolution_patch: ContextPatch | None = None,
        claims_resolved: bool = False,
    ) -> ContextValidationOutcome:
        raw_validation = await self._executor.invoke_tool(
            "validate_context_change",
            ValidateContextChangeInput(
                context=prior,
                proposal=proposal,
                resolution_patch=resolution_patch,
                claims_resolved=claims_resolved,
                turn_id=context.turn_id,
                evidence=evidence,
            ),
            caller=CallerType.RUNTIME,
            context=context,
        )
        if not isinstance(raw_validation, ContextValidationOutcome):
            raise TypeError("Context validation returned an incompatible output.")
        return raw_validation

    @staticmethod
    def _issue_outcome(
        prior: ConversationContext,
        issues: tuple[ContextValidationIssue, ...],
    ) -> ContextValidationOutcome:
        return ContextValidationOutcome(
            status=ContextValidationStatus.NEEDS_CLARIFICATION,
            previous_revision=prior.revision,
            context=prior,
            issues=issues,
        )


class PendingQuestionCoordinator:
    """Synchronize capability clarification state into conversation context."""

    def __init__(
        self,
        *,
        executor: InvocationExecutor,
        repository: ConversationContextRepository,
    ) -> None:
        self._executor = executor
        self._repository = repository

    async def synchronize(
        self,
        *,
        capability_id: str,
        result: dict[str, object],
        context: CapabilityContext,
    ) -> ConversationContext | None:
        if context.conversation_context is None:
            return None
        current = context.conversation_context
        next_questions = current.pending_questions
        if result.get("status") == "needs_input":
            proposed_questions = await self._questions_for_needs_input(
                capability_id=capability_id,
                result=result,
                context=context,
                current=current,
            )
            if proposed_questions is None:
                return None
            next_questions = proposed_questions
        elif result.get("status") == "completed":
            waiting_ids = {
                item.invocation_id
                for item in await context.waiting_invocations(capability_id)
            }
            next_questions = tuple(
                question
                for question in current.pending_questions
                if not (
                    question.capability_id == capability_id
                    and (
                        (
                            question.capability_invocation is not None
                            and question.capability_invocation.invocation_id
                            not in waiting_ids
                        )
                        or (
                            question.capability_invocation is None
                            and not waiting_ids
                        )
                    )
                )
            )
        if next_questions == current.pending_questions:
            return None
        raw_reduction = await self._executor.invoke_tool(
            "reduce_context_patch",
            ReduceContextPatchInput(
                context=current,
                patch=ContextPatch(
                    expected_revision=current.revision,
                    operations=(
                        ReplacePendingQuestionsOperation(questions=next_questions),
                    ),
                ),
                turn_id=context.turn_id,
                evidence="Capability requirement update.",
            ),
            caller=CallerType.RUNTIME,
            context=context,
        )
        if not isinstance(raw_reduction, ContextReduction):
            raise TypeError("Pending context reduction returned an incompatible output.")
        self._repository.save(
            raw_reduction.context,
            expected_revision=raw_reduction.previous_revision,
        )
        return raw_reduction.context

    async def _questions_for_needs_input(
        self,
        *,
        capability_id: str,
        result: dict[str, object],
        context: CapabilityContext,
        current: ConversationContext,
    ) -> tuple[PendingQuestion, ...] | None:
        try:
            reference = CapabilityInvocationReference.model_validate(
                result.get("capability_invocation")
            )
        except Exception:
            return None
        if reference.capability_id != capability_id:
            raise TypeError(
                "Pending capability reference does not match the invoked capability."
            )
        requirements = self._requirements(result.get("fact_requirements"))
        prompt = result.get("prompt")
        if not requirements or not isinstance(prompt, str) or not prompt.strip():
            return current.pending_questions
        waiting = await context.waiting_invocations(capability_id)
        matched = next(
            (item for item in waiting if item.invocation_id == reference.invocation_id),
            None,
        )
        if matched is None:
            raise RuntimeError(
                "A pending question cannot reference a missing waiting invocation."
            )
        if matched.reference() != reference:
            raise RuntimeError(
                "Pending context and waiting invocation metadata do not match."
            )
        if matched.requirements != requirements:
            raise RuntimeError(
                "Pending context and waiting invocation requirements do not match."
            )
        existing = next(
            (
                question
                for question in current.pending_questions
                if question.capability_invocation is not None
                and question.capability_invocation.invocation_id
                == reference.invocation_id
            ),
            None,
        )
        retained = tuple(
            question
            for question in current.pending_questions
            if question is not existing
            and not (
                question.capability_id == capability_id
                and question.capability_invocation is None
                and question.requirements == requirements
            )
        )
        return (
            *retained,
            PendingQuestion(
                question_id=(
                    existing.question_id if existing is not None else uuid4().hex
                ),
                capability_id=capability_id,
                capability_invocation=reference,
                prompt=prompt,
                requirements=requirements,
                created_turn_id=context.turn_id,
                status=PendingQuestionStatus.AWAITING_ANSWER,
            ),
        )

    @staticmethod
    def _requirements(value: object) -> tuple[FactRequirement, ...]:
        if not isinstance(value, list):
            return ()
        requirements: list[FactRequirement] = []
        for item in value:
            try:
                requirements.append(FactRequirement.model_validate(item))
            except Exception:
                continue
        return tuple(requirements)
