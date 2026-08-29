"""Deterministic validation and application of proposed context patches."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from conversation_context.models import (
    AddPendingFactResolutionOperation,
    ConfirmPendingFactResolutionOperation,
    ContextEntity,
    ContextFact,
    ContextPatch,
    ContextReduction,
    ConversationContext,
    EntityReferenceFactValue,
    EntityReferencesFactValue,
    EnsureEntityOperation,
    ExplicitAbsenceAssertion,
    FactDecision,
    FactDecisionStatus,
    FactProvenance,
    FactAssertion,
    FactValue,
    FactResolutionStatus,
    MoneyFactValue,
    MoneyPeriod,
    PendingFactResolution,
    PendingQuestion,
    PendingQuestionStatus,
    PresentAssertion,
    ReplacePendingQuestionsOperation,
    SetFactOperation,
    SetFocusOperation,
)
from conversation_context.quantities import MonetaryExpressionParser
from conversation_context.registry import (
    FactDefinitionRegistry,
    FactUpdatePolicy,
    FactValueKind,
)


class ContextReducer:
    def __init__(self, registry: FactDefinitionRegistry) -> None:
        self._registry = registry
        self._money_parser = MonetaryExpressionParser()

    def reduce(
        self,
        context: ConversationContext,
        patch: ContextPatch,
        *,
        turn_id: str,
        evidence: str,
    ) -> ContextReduction:
        if patch.expected_revision != context.revision:
            raise ValueError(
                "Context patch revision does not match the current context revision."
            )

        next_revision = context.revision + 1
        entities = list(context.entities)
        scopes = list(context.scopes)
        facts = list(context.facts)
        pending = context.pending_questions
        original_pending = context.pending_questions
        pending_resolutions = list(context.pending_fact_resolutions)
        original_pending_resolutions = context.pending_fact_resolutions
        focus = context.focus
        references = {entity.entity_id: entity.entity_id for entity in entities}
        for entity in entities:
            for alias in entity.aliases:
                references.setdefault(alias.casefold(), entity.entity_id)
        decisions: list[FactDecision] = []

        for index, operation in enumerate(patch.operations):
            if isinstance(operation, EnsureEntityOperation):
                existing_id = self._resolve_entity(operation.reference, references)
                if existing_id is not None:
                    decisions.append(
                        FactDecision(
                            operation_index=index,
                            status=FactDecisionStatus.IGNORED,
                            operation=operation.operation,
                            subject_entity_id=existing_id,
                            reason="The entity reference already resolves to a stable entity.",
                        )
                    )
                    continue
                entity_id = "entity:" + uuid5(
                    NAMESPACE_URL,
                    f"{context.conversation_id}:{operation.reference.casefold()}",
                ).hex
                entity = ContextEntity(
                    entity_id=entity_id,
                    kind=operation.kind,
                    aliases=tuple(dict.fromkeys((operation.reference, *operation.aliases))),
                    relationship_to_user=operation.relationship_to_user,
                    created_turn_id=turn_id,
                )
                entities.append(entity)
                if context.focus.scope_id is not None:
                    scopes = [
                        scope.model_copy(
                            update={
                                "subject_entity_ids": tuple(
                                    dict.fromkeys(
                                        (*scope.subject_entity_ids, entity_id)
                                    )
                                )
                            }
                        )
                        if scope.scope_id == context.focus.scope_id
                        else scope
                        for scope in scopes
                    ]
                references[operation.reference.casefold()] = entity_id
                references[entity_id] = entity_id
                for alias in operation.aliases:
                    references.setdefault(alias.casefold(), entity_id)
                decisions.append(
                    FactDecision(
                        operation_index=index,
                        status=FactDecisionStatus.ACCEPTED,
                        operation=operation.operation,
                        subject_entity_id=entity_id,
                        reason="Created a stable context entity.",
                    )
                )
                continue

            if isinstance(operation, SetFocusOperation):
                resolved = tuple(
                    resolved_id
                    for reference in operation.entity_references
                    if (
                        resolved_id := self._resolve_entity(
                            reference,
                            references,
                        )
                    )
                    is not None
                )
                if operation.scope_id is not None and not any(
                    scope.scope_id == operation.scope_id for scope in scopes
                ):
                    decisions.append(
                        FactDecision(
                            operation_index=index,
                            status=FactDecisionStatus.REJECTED,
                            operation=operation.operation,
                            reason="Focus references an unknown context scope.",
                        )
                    )
                    continue
                focus = focus.model_copy(
                    update={
                        "scope_id": operation.scope_id,
                        "entity_ids": resolved,
                    }
                )
                decisions.append(
                    FactDecision(
                        operation_index=index,
                        status=FactDecisionStatus.ACCEPTED,
                        operation=operation.operation,
                        reason="Updated conversational focus.",
                    )
                )
                continue

            if isinstance(operation, ReplacePendingQuestionsOperation):
                pending = operation.questions
                decisions.append(
                    FactDecision(
                        operation_index=index,
                        status=FactDecisionStatus.ACCEPTED,
                        operation=operation.operation,
                        reason="Replaced the typed pending-question set.",
                    )
                )
                continue

            if isinstance(operation, AddPendingFactResolutionOperation):
                proposal_error = self._validate_resolution_proposal(
                    operation.proposal,
                    context=context.model_copy(
                        update={
                            "entities": tuple(entities),
                            "scopes": tuple(scopes),
                            "facts": tuple(facts),
                        }
                    ),
                )
                if proposal_error is not None:
                    decisions.append(
                        FactDecision(
                            operation_index=index,
                            status=FactDecisionStatus.REJECTED,
                            operation=operation.operation,
                            reason=proposal_error,
                        )
                    )
                    continue
                if any(
                    item.proposal_id == operation.proposal.proposal_id
                    for item in pending_resolutions
                ):
                    decisions.append(
                        FactDecision(
                            operation_index=index,
                            status=FactDecisionStatus.IGNORED,
                            operation=operation.operation,
                            reason="The same fact-resolution proposal is already pending.",
                        )
                    )
                    continue
                pending_resolutions = [
                    item
                    for item in pending_resolutions
                    if item.claim_id != operation.proposal.claim_id
                ]
                pending_resolutions.append(operation.proposal)
                decisions.append(
                    FactDecision(
                        operation_index=index,
                        status=FactDecisionStatus.ACCEPTED,
                        operation=operation.operation,
                        reason="Stored a validated fact-resolution proposal.",
                    )
                )
                continue

            if isinstance(operation, ConfirmPendingFactResolutionOperation):
                proposal = next(
                    (
                        item
                        for item in pending_resolutions
                        if item.proposal_id == operation.proposal_id
                    ),
                    None,
                )
                if proposal is None:
                    decisions.append(
                        FactDecision(
                            operation_index=index,
                            status=FactDecisionStatus.REJECTED,
                            operation=operation.operation,
                            reason="The fact-resolution proposal is not pending.",
                        )
                    )
                    continue
                if not operation.accepted:
                    pending_resolutions = [
                        item.model_copy(
                            update={
                                "status": FactResolutionStatus.NEEDS_CLARIFICATION,
                                "prompt": (
                                    "What exact amounts, periods, and household-member "
                                    "assignments should I use instead?"
                                ),
                            }
                        )
                        if item.proposal_id == operation.proposal_id
                        else item
                        for item in pending_resolutions
                    ]
                    decisions.append(
                        FactDecision(
                            operation_index=index,
                            status=FactDecisionStatus.ACCEPTED,
                            operation=operation.operation,
                            reason=(
                                "Rejected the calculated assignment and retained a "
                                "request for an explicit breakdown."
                            ),
                        )
                    )
                    continue
                if (
                    proposal.status is not FactResolutionStatus.AWAITING_CONFIRMATION
                    or len(proposal.assignments) != 1
                ):
                    decisions.append(
                        FactDecision(
                            operation_index=index,
                            status=FactDecisionStatus.REJECTED,
                            operation=operation.operation,
                            reason=(
                                "Only one validated calculated assignment awaiting "
                                "confirmation can be applied."
                            ),
                        )
                    )
                    continue
                assignment = proposal.assignments[0]
                self._ensure_resolution_definition(proposal)
                set_operation = SetFactOperation(
                    definition_key=assignment.definition_key,
                    definition_version=assignment.definition_version,
                    subject_reference=assignment.subject_entity_id,
                    scope_id=assignment.scope_id,
                    assertion=assignment.assertion,
                    correction=assignment.correction,
                )
                decision, fact = self._reduce_fact(
                    set_operation,
                    index=index,
                    context=context.model_copy(
                        update={
                            "entities": tuple(entities),
                            "scopes": tuple(scopes),
                            "facts": tuple(facts),
                        }
                    ),
                    references=references,
                    next_revision=next_revision,
                    turn_id=turn_id,
                    evidence=evidence,
                )
                decisions.append(
                    decision.model_copy(update={"operation": operation.operation})
                )
                if fact is not None:
                    facts.append(fact)
                if decision.status in {
                    FactDecisionStatus.ACCEPTED,
                    FactDecisionStatus.IGNORED,
                    FactDecisionStatus.SUPERSEDED,
                }:
                    pending_resolutions = [
                        item
                        for item in pending_resolutions
                        if item.proposal_id != operation.proposal_id
                    ]
                continue

            if isinstance(operation, SetFactOperation):
                decision, fact = self._reduce_fact(
                    operation,
                    index=index,
                    context=context.model_copy(
                        update={
                            "entities": tuple(entities),
                            "scopes": tuple(scopes),
                            "facts": tuple(facts),
                        }
                    ),
                    references=references,
                    next_revision=next_revision,
                    turn_id=turn_id,
                    evidence=evidence,
                )
                decisions.append(decision)
                if fact is not None:
                    facts.append(fact)

        resolution_context = context.model_copy(
            update={
                "entities": tuple(entities),
                "scopes": tuple(scopes),
                "facts": tuple(facts),
            }
        )
        pending_resolutions = [
            proposal
            for proposal in pending_resolutions
            if not self._is_replaced_by_explicit_facts(
                proposal,
                resolution_context,
            )
        ]

        pending = tuple(
            question.model_copy(
                update={
                    "status": (
                        PendingQuestionStatus.ANSWER_RECEIVED
                        if self._requirements_satisfied(resolution_context, question)
                        else PendingQuestionStatus.AWAITING_ANSWER
                    )
                }
            )
            for question in pending
        )

        changed = any(
            decision.status
            in {FactDecisionStatus.ACCEPTED, FactDecisionStatus.SUPERSEDED}
            for decision in decisions
        ) or tuple(pending_resolutions) != original_pending_resolutions or pending != original_pending
        updated = context.model_copy(
            update={
                "revision": next_revision if changed else context.revision,
                "entities": tuple(entities),
                "scopes": tuple(scopes),
                "facts": tuple(facts),
                "pending_questions": pending,
                "pending_fact_resolutions": tuple(pending_resolutions),
                "focus": focus,
                "updated_at": datetime.now(timezone.utc),
            }
        )
        return ContextReduction(
            previous_revision=context.revision,
            context=updated,
            decisions=tuple(decisions),
        )

    @staticmethod
    def _requirements_satisfied(
        context: ConversationContext,
        question: PendingQuestion,
    ) -> bool:
        if not question.requirements:
            return False
        for requirement in question.requirements:
            subject_ids: tuple[str, ...]
            if requirement.subject_entity_id is not None:
                subject_ids = (requirement.subject_entity_id,)
            elif requirement.subject_kind is not None:
                subject_ids = tuple(
                    entity.entity_id
                    for entity in context.entities
                    if entity.kind is requirement.subject_kind
                )
            else:
                return False
            matching = tuple(
                fact
                for subject_id in subject_ids
                if (
                    fact := context.active_fact(
                        requirement.fact_key,
                        subject_id,
                        requirement.scope_id,
                    )
                )
                is not None
            )
            if not matching:
                return False
            if (
                not requirement.allow_explicit_absence
                and all(
                    isinstance(fact.assertion, ExplicitAbsenceAssertion)
                    for fact in matching
                )
            ):
                return False
        return True

    def _is_replaced_by_explicit_facts(
        self,
        proposal: PendingFactResolution,
        context: ConversationContext,
    ) -> bool:
        """Drop a disputed proposal only when accepted facts satisfy its constraint."""

        expected_total = proposal.expected_total
        period = proposal.period
        definition_key: str | None = None
        if proposal.assignments:
            definition_keys = {
                assignment.definition_key for assignment in proposal.assignments
            }
            if len(definition_keys) == 1:
                definition_key = next(iter(definition_keys))
        if definition_key is None and proposal.variable_name is not None:
            definition = self._registry.find_by_engine_binding(
                proposal.variable_name,
                entity=proposal.variable_entity,
            )
            if definition is not None:
                definition_key = definition.key
        if definition_key is not None and proposal.terms:
            if period is None or expected_total is None:
                return False
            total = Decimal("0")
            for term in proposal.terms:
                fact = context.active_fact(
                    definition_key,
                    term.subject_entity_id,
                    proposal.scope_id,
                )
                value = self._money_fact_value(fact, period)
                if value is None:
                    value = term.known_value
                if value is None:
                    return False
                total += term.coefficient * value
            return total == expected_total

        # Version-one reconciliation could write an incomplete proposal directly.
        # Repair only when later explicit facts give every referenced entity the same
        # registered monetary fact and exactly satisfy the stated total. The original
        # implementation omitted the typed total and period on this failure path, so
        # recover those only when the evidence and accepted facts are unambiguous.
        if proposal.variable_name is not None or proposal.terms:
            return False
        candidate_keys: set[str] | None = None
        active_facts = context.active_facts()
        for entity_id in proposal.referenced_entity_ids:
            keys = {
                fact.definition_key
                for fact in active_facts
                if fact.subject_entity_id == entity_id
                and fact.scope_id == proposal.scope_id
                and fact.introduced_revision > proposal.created_revision
                and isinstance(fact.assertion, PresentAssertion)
                and isinstance(fact.assertion.value, MoneyFactValue)
            }
            candidate_keys = keys if candidate_keys is None else candidate_keys & keys
        if candidate_keys is None or len(candidate_keys) != 1:
            return False
        common_key = next(iter(candidate_keys))
        facts: list[ContextFact] = []
        for entity_id in proposal.referenced_entity_ids:
            fact = context.active_fact(common_key, entity_id, proposal.scope_id)
            if fact is None:
                return False
            facts.append(fact)

        if period is None:
            fact_periods = {
                fact.assertion.value.period
                for fact in facts
                if isinstance(fact.assertion, PresentAssertion)
                and isinstance(fact.assertion.value, MoneyFactValue)
            }
            if len(fact_periods) != 1:
                return False
            period = next(iter(fact_periods))
        if expected_total is None:
            expressions = self._money_parser.extract(proposal.evidence)
            if len(expressions) != 1:
                return False
            expected_total = expressions[0].amount

        total = Decimal("0")
        for fact in facts:
            value = self._money_fact_value(fact, period)
            if value is None:
                return False
            total += value
        return total == expected_total

    @staticmethod
    def _money_fact_value(
        fact: ContextFact | None,
        period: MoneyPeriod,
    ) -> Decimal | None:
        if fact is None or not isinstance(fact.assertion, PresentAssertion):
            return None
        value = fact.assertion.value
        if not isinstance(value, MoneyFactValue):
            return None
        periods_per_year = {
            MoneyPeriod.ANNUAL: Decimal("1"),
            MoneyPeriod.MONTHLY: Decimal("12"),
            MoneyPeriod.FOUR_WEEKLY: Decimal("13"),
            MoneyPeriod.WEEKLY: Decimal("52"),
        }
        annual = value.amount * periods_per_year[value.period]
        return annual / periods_per_year[period]

    def _validate_resolution_proposal(
        self,
        proposal: PendingFactResolution,
        *,
        context: ConversationContext,
    ) -> str | None:
        entity_ids = {entity.entity_id for entity in context.entities}
        scope_ids = {scope.scope_id for scope in context.scopes}
        if proposal.scope_id not in scope_ids:
            return "The fact-resolution proposal references an unknown scope."
        if not set(proposal.referenced_entity_ids) <= entity_ids:
            return "The fact-resolution proposal references an unknown entity."
        if proposal.status is FactResolutionStatus.AWAITING_CONFIRMATION:
            if len(proposal.assignments) != 1:
                return "A confirmable resolution must contain exactly one assignment."
            if (
                proposal.variable_name is None
                or proposal.variable_entity is None
                or proposal.variable_label is None
                or proposal.expected_total is None
                or proposal.period is None
                or not proposal.terms
            ):
                return (
                    "A confirmable resolution must contain its validated variable, "
                    "period, equation terms, and expected total."
                )
            if any(
                term.variable_name != proposal.variable_name
                or term.subject_entity_id not in entity_ids
                for term in proposal.terms
            ):
                return "A confirmable resolution contains an invalid equation term."
            assignment = proposal.assignments[0]
            unresolved_subjects = {
                term.subject_entity_id
                for term in proposal.terms
                if term.known_value is None
            }
            if (
                proposal.relationship.value == "sum"
                and unresolved_subjects != {assignment.subject_entity_id}
            ) or (
                proposal.relationship.value == "direct"
                and {term.subject_entity_id for term in proposal.terms}
                != {assignment.subject_entity_id}
            ):
                return "The calculated assignment does not target the unresolved term."
            assignment_value = assignment.assertion.value
            if (
                not isinstance(assignment_value, MoneyFactValue)
                or assignment_value.period is not proposal.period
            ):
                return "The calculated assignment does not use the validated period."
            equation_total = sum(
                (
                    term.coefficient
                    * (
                        assignment_value.amount
                        if term.subject_entity_id == assignment.subject_entity_id
                        else term.known_value or Decimal("0")
                    )
                    for term in proposal.terms
                ),
                start=Decimal("0"),
            )
            if equation_total != proposal.expected_total:
                return "The calculated assignment does not satisfy the validated total."
            self._ensure_resolution_definition(proposal)
        for assignment in proposal.assignments:
            if assignment.subject_entity_id not in entity_ids:
                return "A calculated assignment references an unknown entity."
            if assignment.scope_id not in scope_ids:
                return "A calculated assignment references an unknown scope."
            try:
                definition = self._registry.get(
                    assignment.definition_key,
                    assignment.definition_version,
                )
            except KeyError:
                return "A calculated assignment references an unknown fact definition."
            entity = next(
                item
                for item in context.entities
                if item.entity_id == assignment.subject_entity_id
            )
            if entity.kind not in definition.subject_kinds:
                return "A calculated assignment targets an incompatible entity type."
            error = definition.validate_value(assignment.assertion.value)
            if error is not None:
                return error
        return None

    def _ensure_resolution_definition(
        self,
        proposal: PendingFactResolution,
    ) -> None:
        if not proposal.assignments:
            return
        assignment = proposal.assignments[0]
        try:
            self._registry.get(
                assignment.definition_key,
                assignment.definition_version,
            )
            return
        except KeyError:
            pass
        if (
            proposal.variable_name is None
            or proposal.variable_entity is None
            or proposal.variable_label is None
        ):
            return
        self._registry.ensure_engine_definition(
            variable_name=proposal.variable_name,
            entity=proposal.variable_entity,
            label=proposal.variable_label,
            value_kind=FactValueKind.MONEY,
        )

    def _reduce_fact(
        self,
        operation: SetFactOperation,
        *,
        index: int,
        context: ConversationContext,
        references: dict[str, str],
        next_revision: int,
        turn_id: str,
        evidence: str,
    ) -> tuple[FactDecision, ContextFact | None]:
        subject_id = self._resolve_entity(operation.subject_reference, references)
        if subject_id is None:
            return self._decision(
                index,
                operation,
                FactDecisionStatus.REJECTED,
                "The subject reference does not resolve to a stable entity.",
            ), None
        try:
            definition = self._registry.get(
                operation.definition_key,
                operation.definition_version,
            )
        except KeyError:
            return self._decision(
                index,
                operation,
                FactDecisionStatus.REJECTED,
                "The fact definition is not registered.",
                subject_id,
            ), None
        entity = next(item for item in context.entities if item.entity_id == subject_id)
        if entity.kind not in definition.subject_kinds:
            return self._decision(
                index,
                operation,
                FactDecisionStatus.REJECTED,
                "The fact definition does not permit this subject entity type.",
                subject_id,
            ), None
        if not any(scope.scope_id == operation.scope_id for scope in context.scopes):
            return self._decision(
                index,
                operation,
                FactDecisionStatus.REJECTED,
                "The fact references an unknown context scope.",
                subject_id,
            ), None
        if isinstance(operation.assertion, ExplicitAbsenceAssertion):
            if not definition.allow_explicit_absence:
                return self._decision(
                    index,
                    operation,
                    FactDecisionStatus.REJECTED,
                    "The fact definition does not permit explicit absence.",
                    subject_id,
                ), None
        elif isinstance(operation.assertion, PresentAssertion):
            value_error = definition.validate_value(operation.assertion.value)
            if value_error is not None:
                return self._decision(
                    index,
                    operation,
                    FactDecisionStatus.REJECTED,
                    value_error,
                    subject_id,
                ), None
            reference_error = self._validate_value_references(
                operation.assertion.value,
                references,
            )
            if reference_error is not None:
                return self._decision(
                    index,
                    operation,
                    FactDecisionStatus.REJECTED,
                    reference_error,
                    subject_id,
                ), None

        normalized_assertion = self._normalize_value_references(
            operation.assertion,
            references,
        )

        current = context.active_fact(
            operation.definition_key,
            subject_id,
            operation.scope_id,
        )
        if current is not None and current.assertion == normalized_assertion:
            return self._decision(
                index,
                operation,
                FactDecisionStatus.IGNORED,
                "The same fact assertion is already active.",
                subject_id,
                fact_id=current.fact_id,
            ), None
        if (
            current is not None
            and not operation.correction
            and definition.update_policy
            is FactUpdatePolicy.REQUIRE_EXPLICIT_CORRECTION
        ):
            return self._decision(
                index,
                operation,
                FactDecisionStatus.CONFLICTED,
                "A different fact is active and the proposal is not an explicit correction.",
                subject_id,
                fact_id=current.fact_id,
            ), None

        fact = ContextFact(
            definition_key=operation.definition_key,
            definition_version=operation.definition_version,
            subject_entity_id=subject_id,
            scope_id=operation.scope_id,
            assertion=normalized_assertion,
            provenance=FactProvenance(
                turn_id=turn_id,
                evidence=evidence,
            ),
            introduced_revision=next_revision,
            supersedes_fact_id=current.fact_id if current is not None else None,
        )
        status = (
            FactDecisionStatus.SUPERSEDED
            if current is not None
            else FactDecisionStatus.ACCEPTED
        )
        return self._decision(
            index,
            operation,
            status,
            "Accepted a validated fact assertion."
            if current is None
            else (
                "Accepted an explicit correction and superseded the active fact."
                if operation.correction
                else "Accepted a new explicit assertion and superseded the active fact."
            ),
            subject_id,
            fact_id=fact.fact_id,
            superseded_fact_id=current.fact_id if current is not None else None,
        ), fact

    @staticmethod
    def _resolve_entity(reference: str, references: dict[str, str]) -> str | None:
        return references.get(reference) or references.get(reference.casefold())

    @staticmethod
    def _validate_value_references(
        value: FactValue,
        references: dict[str, str],
    ) -> str | None:
        if isinstance(value, EntityReferenceFactValue):
            if ContextReducer._resolve_entity(value.entity_id, references) is None:
                return "The fact value references an unknown entity."
        if isinstance(value, EntityReferencesFactValue):
            if any(
                ContextReducer._resolve_entity(item, references) is None
                for item in value.entity_ids
            ):
                return "The fact value references an unknown entity."
        return None

    @staticmethod
    def _normalize_value_references(
        assertion: FactAssertion,
        references: dict[str, str],
    ) -> FactAssertion:
        if not isinstance(assertion, PresentAssertion):
            return assertion
        value = assertion.value
        if isinstance(value, EntityReferenceFactValue):
            resolved_id = ContextReducer._resolve_entity(value.entity_id, references)
            if resolved_id is not None:
                return assertion.model_copy(
                    update={
                        "value": value.model_copy(
                            update={"entity_id": resolved_id}
                        )
                    }
                )
        if isinstance(value, EntityReferencesFactValue):
            resolved_ids = tuple(
                ContextReducer._resolve_entity(item, references) or item
                for item in value.entity_ids
            )
            return assertion.model_copy(
                update={
                    "value": value.model_copy(
                        update={"entity_ids": resolved_ids}
                    )
                }
            )
        return assertion

    @staticmethod
    def _decision(
        index: int,
        operation: SetFactOperation,
        status: FactDecisionStatus,
        reason: str,
        subject_id: str | None = None,
        *,
        fact_id: str | None = None,
        superseded_fact_id: str | None = None,
    ) -> FactDecision:
        return FactDecision(
            operation_index=index,
            status=status,
            operation=operation.operation,
            definition_key=operation.definition_key,
            subject_entity_id=subject_id,
            fact_id=fact_id,
            superseded_fact_id=superseded_fact_id,
            reason=reason,
        )
