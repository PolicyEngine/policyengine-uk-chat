"""Validation and atomic application of model-proposed context changes."""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
import re

from pydantic import BaseModel, ConfigDict, ValidationError

from conversation_context.models import (
    ClaimedMoneyValue,
    ConfirmPendingFactResolutionOperation,
    ContextEntityCandidate,
    ContextFocusCandidate,
    ContextOperation,
    ContextPatch,
    ContextReduction,
    ConversationContext,
    EnsureEntityOperation,
    ExplicitAbsenceAssertion,
    FactClaim,
    FactClaimFieldUpdate,
    FactClaimRelationship,
    FactAssertion,
    FactDecision,
    FactDecisionStatus,
    FactResolutionStatus,
    IntegerFactValue,
    MoneyFactValue,
    MoneyPeriod,
    PendingFactResolution,
    PendingFactResolutionResponse,
    PendingResolutionAction,
    PresentAssertion,
    ProposedContextChange,
    SetFactOperation,
    SetFocusOperation,
    TextFactValue,
    TextSetFactValue,
)
from conversation_context.reducer import ContextReducer
from conversation_context.registry import FactDefinitionRegistry
from conversation_context.repository import ConversationContextRepository
from conversation_context.quantities import MonetaryExpressionParser


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ContextChangeProposal(StrictModel):
    """One model-authored interpretation with no persistence operations."""

    expected_revision: int
    candidate_entities: tuple[ContextEntityCandidate, ...] = ()
    changes: tuple[ProposedContextChange, ...] = ()
    focus: ContextFocusCandidate | None = None

    @property
    def claims(self) -> tuple[FactClaim, ...]:
        return tuple(change for change in self.changes if isinstance(change, FactClaim))

    @property
    def proposal_responses(self) -> tuple[PendingFactResolutionResponse, ...]:
        return tuple(
            change
            for change in self.changes
            if isinstance(change, PendingFactResolutionResponse)
        )

    def change_index(self, change_id: str) -> int | None:
        return next(
            (
                index
                for index, change in enumerate(self.changes)
                if (
                    isinstance(change, FactClaim)
                    and change.claim_id == change_id
                )
                or (
                    isinstance(change, PendingFactResolutionResponse)
                    and change.response_id == change_id
                )
            ),
            None,
        )


class ContextValidationIssue(StrictModel):
    """Machine-readable feedback for interpretation repair and debug output."""

    code: str
    message: str
    path: tuple[str, ...] = ()
    claim_index: int | None = None
    operation_index: int | None = None
    evidence: str | None = None


class SemanticClaimReview(StrictModel):
    """One model-authored semantic verdict retained in validation debug output."""

    claim_id: str
    supported: bool
    reason: str
    evidence: str


class ContextValidationStatus(str, Enum):
    RESOLUTION_REQUIRED = "resolution_required"
    READY_TO_COMMIT = "ready_to_commit"
    NEEDS_CLARIFICATION = "needs_clarification"
    NO_CHANGE = "no_change"


class ContextValidationOutcome(StrictModel):
    """The complete deterministic result for one proposed context change."""

    status: ContextValidationStatus
    previous_revision: int
    context: ConversationContext
    generated_operations: tuple[ContextOperation, ...] = ()
    claims_to_resolve: tuple[FactClaim, ...] = ()
    decisions: tuple[FactDecision, ...] = ()
    issues: tuple[ContextValidationIssue, ...] = ()
    semantic_reviews: tuple[SemanticClaimReview, ...] = ()

    @property
    def committable(self) -> bool:
        return self.status is ContextValidationStatus.READY_TO_COMMIT


class ValidateContextChangeInput(StrictModel):
    context: ConversationContext
    proposal: ContextChangeProposal
    resolution_patch: ContextPatch | None = None
    claims_resolved: bool = False
    turn_id: str
    evidence: str


class ContextChangeValidator:
    """Validate one complete model proposal without choosing semantic meaning."""

    _accepted_statuses = {
        FactDecisionStatus.ACCEPTED,
        FactDecisionStatus.IGNORED,
        FactDecisionStatus.SUPERSEDED,
    }

    def __init__(
        self,
        reducer: ContextReducer,
        registry: FactDefinitionRegistry,
        monetary_parser: MonetaryExpressionParser | None = None,
    ) -> None:
        self._reducer = reducer
        self._registry = registry
        self._monetary_parser = monetary_parser or MonetaryExpressionParser()

    def validate(
        self,
        validation_input: ValidateContextChangeInput,
        *,
        semantic_issues: tuple[ContextValidationIssue, ...] = (),
    ) -> ContextValidationOutcome:
        prior = validation_input.context
        proposal = validation_input.proposal
        if proposal.expected_revision != prior.revision:
            return self._invalid(
                prior,
                issues=(
                    ContextValidationIssue(
                        code="stale_proposal_revision",
                        path=("proposal", "expected_revision"),
                        message=(
                            "The interpreted context revision does not match the "
                            "currently loaded conversation context."
                        ),
                    ),
                ),
            )

        grounding_issues = self._grounding_issues(
            validation_input.evidence,
            proposal,
        )

        generated, claims_to_resolve, planning_issues = self._plan(
            prior,
            proposal,
            current_message=validation_input.evidence,
        )

        generated_patch = ContextPatch(
            expected_revision=prior.revision,
            operations=generated,
        )
        provisional = self._reduce(
            prior,
            generated_patch,
            turn_id=validation_input.turn_id,
            evidence=validation_input.evidence,
        )
        if isinstance(provisional, tuple):
            return self._invalid(
                prior,
                generated_operations=generated,
                claims_to_resolve=claims_to_resolve,
                issues=provisional,
            )
        direct_issues = self._decision_issues(provisional.decisions)
        aggregate_issues = self._aggregate_issues(provisional.context)
        validation_issues = (
            *semantic_issues,
            *grounding_issues,
            *planning_issues,
            *direct_issues,
            *aggregate_issues,
        )
        if validation_issues:
            return self._invalid(
                prior,
                generated_operations=generated,
                claims_to_resolve=claims_to_resolve,
                decisions=provisional.decisions,
                issues=validation_issues,
            )

        if claims_to_resolve and not validation_input.claims_resolved:
            resolution_issues = tuple(
                self._claim_issue(
                    self._claim_change_index(prior, proposal, claim),
                    claim,
                    "authoritative_resolution_required",
                    (
                        "The model-proposed claim requires an authoritative semantic "
                        "mapping before it can be validated as a context change."
                    ),
                )
                for claim in claims_to_resolve
            )
            return ContextValidationOutcome(
                status=ContextValidationStatus.RESOLUTION_REQUIRED,
                previous_revision=prior.revision,
                context=provisional.context,
                generated_operations=generated,
                claims_to_resolve=claims_to_resolve,
                decisions=provisional.decisions,
                issues=resolution_issues,
            )

        if claims_to_resolve and validation_input.resolution_patch is None:
            return self._invalid(
                prior,
                generated_operations=generated,
                claims_to_resolve=claims_to_resolve,
                decisions=provisional.decisions,
                issues=(
                    ContextValidationIssue(
                        code="fact_claims_not_resolved",
                        path=("proposal", "changes"),
                        message=(
                            "The proposal contains claims requiring authoritative "
                            "resolution but no validated resolution operations."
                        ),
                    ),
                ),
            )

        resolution_patch = validation_input.resolution_patch
        if resolution_patch is not None:
            if resolution_patch.expected_revision != provisional.context.revision:
                return self._invalid(
                    prior,
                    generated_operations=generated,
                    claims_to_resolve=claims_to_resolve,
                    decisions=provisional.decisions,
                    issues=(
                        ContextValidationIssue(
                            code="stale_resolution_revision",
                            path=("resolution_patch", "expected_revision"),
                            message=(
                                "Fact-resolution operations were not derived from the "
                                "validated provisional context."
                            ),
                        ),
                    ),
                )
            combined_operations = (*generated, *resolution_patch.operations)
            combined_patch = ContextPatch(
                expected_revision=prior.revision,
                operations=combined_operations,
            )
            combined = self._reduce(
                prior,
                combined_patch,
                turn_id=validation_input.turn_id,
                evidence=validation_input.evidence,
            )
            if isinstance(combined, tuple):
                return self._invalid(
                    prior,
                    generated_operations=combined_operations,
                    claims_to_resolve=claims_to_resolve,
                    issues=combined,
                )
        else:
            combined_operations = generated
            combined = provisional

        decision_issues = self._decision_issues(combined.decisions)
        if decision_issues:
            return self._invalid(
                prior,
                generated_operations=combined_operations,
                claims_to_resolve=claims_to_resolve,
                decisions=combined.decisions,
                issues=decision_issues,
            )
        final_issues = self._aggregate_issues(combined.context)
        if final_issues:
            return self._invalid(
                prior,
                generated_operations=combined_operations,
                claims_to_resolve=claims_to_resolve,
                decisions=combined.decisions,
                issues=final_issues,
            )
        if combined.context.revision == prior.revision:
            return ContextValidationOutcome(
                status=ContextValidationStatus.NO_CHANGE,
                previous_revision=prior.revision,
                context=prior,
                generated_operations=combined_operations,
                claims_to_resolve=claims_to_resolve,
                decisions=combined.decisions,
            )
        if combined.context.revision != prior.revision + 1:
            return self._invalid(
                prior,
                generated_operations=combined_operations,
                claims_to_resolve=claims_to_resolve,
                decisions=combined.decisions,
                issues=(
                    ContextValidationIssue(
                        code="invalid_validated_revision",
                        path=("context", "revision"),
                        message="A current-message update must create exactly one revision.",
                    ),
                ),
            )
        return ContextValidationOutcome(
            status=ContextValidationStatus.READY_TO_COMMIT,
            previous_revision=prior.revision,
            context=combined.context,
            generated_operations=combined_operations,
            claims_to_resolve=claims_to_resolve,
            decisions=combined.decisions,
        )

    def _plan(
        self,
        context: ConversationContext,
        proposal: ContextChangeProposal,
        *,
        current_message: str,
    ) -> tuple[
        tuple[ContextOperation, ...],
        tuple[FactClaim, ...],
        tuple[ContextValidationIssue, ...],
    ]:
        operations: list[ContextOperation] = [
            EnsureEntityOperation(
                reference=candidate.reference,
                kind=candidate.kind,
                aliases=candidate.aliases,
                relationship_to_user=candidate.relationship_to_user,
            )
            for candidate in proposal.candidate_entities
        ]
        claims_to_resolve: list[FactClaim] = []
        issues: list[ContextValidationIssue] = []
        accepted_claim_ids: set[str] = set()
        planned_facts: dict[tuple[str, str, str, str], FactAssertion] = {}
        active_scope = context.focus.scope_id

        pending_resolutions = {
            pending.proposal_id: pending for pending in context.pending_fact_resolutions
        }
        seen_response_ids: set[str] = set()
        valid_responses: list[PendingFactResolutionResponse] = []
        for response in proposal.proposal_responses:
            index = proposal.change_index(response.response_id)
            assert index is not None
            valid = True
            if response.proposal_id in seen_response_ids:
                valid = False
                issues.append(
                    ContextValidationIssue(
                        code="duplicate_pending_resolution_response",
                        message=(
                            "The proposal responds to the same pending fact resolution "
                            "more than once."
                        ),
                        path=("proposal", "changes", str(index)),
                    )
                )
            elif response.proposal_id not in pending_resolutions:
                valid = False
                issues.append(
                    ContextValidationIssue(
                        code="unknown_pending_resolution_response",
                        message=(
                            "The proposal response does not identify an existing "
                            "pending fact-resolution proposal."
                        ),
                        path=("proposal", "changes", str(index)),
                    )
                )
            else:
                pending = pending_resolutions[response.proposal_id]
                if (
                    response.action is not PendingResolutionAction.SUPPLY
                    and not self._text_is_grounded(response.evidence, current_message)
                ):
                    valid = False
                    issues.append(
                        ContextValidationIssue(
                            code="uncited_pending_resolution_response",
                            message=(
                                "The pending-resolution response evidence is absent "
                                "from the exact current message."
                            ),
                            path=("proposal", "changes", str(index), "evidence"),
                            evidence=response.evidence,
                        )
                    )
                if response.action is PendingResolutionAction.SUPPLY:
                    merged, response_issues = self._merge_pending_claim(
                        pending,
                        response,
                        current_message=current_message,
                        change_index=index,
                    )
                    issues.extend(response_issues)
                    valid = valid and merged is not None and not response_issues
                    if valid and merged is not None:
                        claims_to_resolve.append(merged)
                elif pending.status is not FactResolutionStatus.AWAITING_CONFIRMATION:
                    valid = False
                    issues.append(
                        ContextValidationIssue(
                            code="pending_resolution_not_confirmable",
                            message=(
                                "This pending record requests clarification. Accept or "
                                "reject applies only to a calculated assignment awaiting "
                                "confirmation; supply the missing source-claim fields."
                            ),
                            path=("proposal", "changes", str(index)),
                        )
                    )
            if valid:
                valid_responses.append(response)
            seen_response_ids.add(response.proposal_id)

        for claim in proposal.claims:
            index = proposal.change_index(claim.claim_id)
            assert index is not None
            scope_id = claim.scope_id or active_scope
            if scope_id is None:
                issues.append(
                    self._claim_issue(
                        index,
                        claim,
                        "missing_claim_scope",
                        "The fact claim has no active or explicit context scope.",
                    )
                )
                continue
            if claim.relationship is not FactClaimRelationship.DIRECT:
                claims_to_resolve.append(claim)
                continue
            definition_key = claim.definition_key
            definition_version = claim.definition_version
            inherited_period = None
            if isinstance(claim.value, ClaimedMoneyValue) and claim.value.period is None:
                inherited = self._periodless_direct_binding(context, claim, scope_id)
                if inherited is None:
                    claims_to_resolve.append(claim)
                    continue
                definition_key, definition_version, inherited_period = inherited
            if definition_key is None:
                if isinstance(claim.value, ClaimedMoneyValue):
                    claims_to_resolve.append(claim)
                else:
                    issues.append(
                        self._claim_issue(
                            index,
                            claim,
                            "unmapped_fact_claim",
                            (
                                "A non-monetary direct claim must select one supplied "
                                "registered fact definition."
                            ),
                        )
                    )
                continue
            try:
                self._registry.get(definition_key, definition_version)
            except KeyError:
                issues.append(
                    self._claim_issue(
                        index,
                        claim,
                        "unregistered_fact_claim",
                        "The fact claim selects an unregistered fact definition.",
                    )
                )
                continue
            assertion: FactAssertion
            if claim.explicit_absence:
                assertion = ExplicitAbsenceAssertion()
            elif isinstance(claim.value, ClaimedMoneyValue):
                period = claim.value.period or inherited_period
                if period is None:
                    issues.append(
                        self._claim_issue(
                            index,
                            claim,
                            "unresolved_fact_period",
                            (
                                "The monetary fact claim has no validated period and "
                                "must remain available for authoritative resolution."
                            ),
                        )
                    )
                    continue
                assertion = PresentAssertion(
                    value=MoneyFactValue(
                        amount=claim.value.amount,
                        period=period,
                        currency=claim.value.currency,
                    )
                )
            elif claim.value is not None:
                assertion = PresentAssertion(value=claim.value)
            else:
                issues.append(
                    self._claim_issue(
                        index,
                        claim,
                        "missing_fact_claim_value",
                        "The fact claim contains neither a value nor explicit absence.",
                    )
                )
                continue
            fact_target = (
                definition_key,
                definition_version,
                self._resolve_subject_reference(
                    context,
                    claim.subject_references[0],
                )
                or claim.subject_references[0].strip().casefold(),
                scope_id,
            )
            previous_assertion = planned_facts.get(fact_target)
            if previous_assertion is not None:
                if previous_assertion != assertion:
                    issues.append(
                        self._claim_issue(
                            index,
                            claim,
                            "conflicting_fact_claims",
                            (
                                "Two claims in the same proposal assign different values "
                                "to the same registered fact and subject."
                            ),
                        )
                    )
                else:
                    accepted_claim_ids.add(claim.claim_id)
                continue
            planned_facts[fact_target] = assertion
            accepted_claim_ids.add(claim.claim_id)
            operations.append(
                SetFactOperation(
                    definition_key=definition_key,
                    definition_version=definition_version,
                    subject_reference=claim.subject_references[0],
                    scope_id=scope_id,
                    assertion=assertion,
                    correction=claim.correction,
                )
            )

        resolved_claim_ids = {claim.claim_id for claim in claims_to_resolve}
        issue_indexes = {
            int(issue.path[2])
            for issue in issues
            if len(issue.path) >= 3
            and issue.path[:2] == ("proposal", "changes")
            and issue.path[2].isdigit()
        }
        for claim in proposal.claims:
            index = proposal.change_index(claim.claim_id)
            assert index is not None
            if (
                claim.claim_id not in accepted_claim_ids
                and claim.claim_id not in resolved_claim_ids
                and index not in issue_indexes
            ):
                issues.append(
                    self._claim_issue(
                        index,
                        claim,
                        "unaccounted_fact_claim",
                        (
                            "Validation did not produce an operation, authoritative "
                            "resolution request, or claim-specific issue."
                        ),
                    )
                )

        if proposal.focus is not None:
            operations.append(
                SetFocusOperation(
                    scope_id=proposal.focus.scope_id,
                    entity_references=proposal.focus.entity_references,
                )
            )
        operations.extend(
            ConfirmPendingFactResolutionOperation(
                proposal_id=response.proposal_id,
                accepted=response.action is PendingResolutionAction.ACCEPT,
            )
            for response in valid_responses
            if response.action
            in {PendingResolutionAction.ACCEPT, PendingResolutionAction.REJECT}
        )
        return tuple(operations), tuple(claims_to_resolve), tuple(issues)

    def _merge_pending_claim(
        self,
        pending: PendingFactResolution,
        response: PendingFactResolutionResponse,
        *,
        current_message: str,
        change_index: int,
    ) -> tuple[FactClaim | None, tuple[ContextValidationIssue, ...]]:
        base_path = ("proposal", "changes", str(change_index))
        issues: list[ContextValidationIssue] = []
        if pending.status is not FactResolutionStatus.NEEDS_CLARIFICATION:
            issues.append(
                ContextValidationIssue(
                    code="pending_resolution_not_supplementable",
                    message=(
                        "Only a pending resolution that requests clarification can "
                        "receive source-claim field updates."
                    ),
                    path=base_path,
                    evidence=response.evidence,
                )
            )
        if pending.source_claim is None:
            issues.append(
                ContextValidationIssue(
                    code="pending_resolution_missing_source_claim",
                    message=(
                        "The referenced pending resolution predates retained source "
                        "claims and cannot accept a partial field update."
                    ),
                    path=base_path,
                    evidence=response.evidence,
                )
            )
        if not self._text_is_grounded(response.evidence, current_message):
            issues.append(
                ContextValidationIssue(
                    code="uncited_pending_resolution_response",
                    message=(
                        "The pending-resolution response evidence is absent from the "
                        "exact current message."
                    ),
                    path=(*base_path, "evidence"),
                    evidence=response.evidence,
                )
            )
        if issues or pending.source_claim is None:
            return None, tuple(issues)

        claim_data = pending.source_claim.model_dump(mode="python")
        seen_paths: set[tuple[str, ...]] = set()
        immutable_roots = {"kind", "claim_id", "evidence"}
        for update_index, update in enumerate(response.updates):
            update_path = (*base_path, "updates", str(update_index))
            if update.path in seen_paths:
                issues.append(
                    ContextValidationIssue(
                        code="duplicate_fact_claim_field_update",
                        message="A retained fact-claim field is updated more than once.",
                        path=(*update_path, "path"),
                        evidence=update.evidence,
                    )
                )
                continue
            seen_paths.add(update.path)
            if update.path[0] in immutable_roots:
                issues.append(
                    ContextValidationIssue(
                        code="immutable_fact_claim_field_update",
                        message=(
                            "A pending-resolution response cannot replace claim "
                            "identity or original source evidence."
                        ),
                        path=(*update_path, "path"),
                        evidence=update.evidence,
                    )
                )
                continue
            if not self._text_is_grounded(update.evidence, current_message):
                issues.append(
                    ContextValidationIssue(
                        code="uncited_fact_claim_field_update",
                        message=(
                            "The field update's cited evidence is absent from the "
                            "exact current message."
                        ),
                        path=(*update_path, "evidence"),
                        evidence=update.evidence,
                    )
                )
                continue
            target: object = claim_data
            for field in update.path[:-1]:
                if not isinstance(target, dict) or field not in target:
                    target = None
                    break
                target = target[field]
            final_field = update.path[-1]
            if not isinstance(target, dict) or final_field not in target:
                issues.append(
                    ContextValidationIssue(
                        code="unknown_fact_claim_field_update",
                        message=(
                            "The supplied path does not identify a field in the "
                            "retained fact-claim schema."
                        ),
                        path=(*update_path, "path"),
                        evidence=update.evidence,
                    )
                )
                continue
            if not self._supplement_value_is_grounded(
                update,
                current_message=current_message,
            ):
                issues.append(
                    ContextValidationIssue(
                        code="ungrounded_fact_claim_field_update",
                        message=(
                            "The supplied field value is not supported by the exact "
                            "current message."
                        ),
                        path=(*update_path, "value"),
                        evidence=update.evidence,
                    )
                )
                continue
            target[final_field] = update.value

        if issues:
            return None, tuple(issues)
        try:
            return FactClaim.model_validate(claim_data), ()
        except ValidationError as exc:
            return None, tuple(
                ContextValidationIssue(
                    code="invalid_merged_fact_claim",
                    message=error["msg"],
                    path=(*base_path, "updates", *(str(item) for item in error["loc"])),
                    evidence=response.evidence,
                )
                for error in exc.errors(include_url=False, include_input=False)
            )

    def _supplement_value_is_grounded(
        self,
        update: FactClaimFieldUpdate,
        *,
        current_message: str,
    ) -> bool:
        value = update.value
        if update.path[-1] == "amount":
            try:
                amount = Decimal(str(value))
            except Exception:
                return False
            return amount in {
                expression.amount
                for expression in self._monetary_parser.extract(current_message)
            }
        if update.path[-1] == "period" and isinstance(value, str):
            period_tokens = {
                "annual": ("annual", "annually", "year", "yearly", "per year"),
                "monthly": ("month", "monthly", "per month"),
                "four_weekly": (
                    "four weekly",
                    "four-weekly",
                    "every four weeks",
                    "per four weeks",
                ),
                "weekly": ("week", "weekly", "per week"),
            }
            normalized_message = current_message.casefold()
            return any(
                token in normalized_message
                for token in period_tokens.get(value.casefold(), ())
            )
        if isinstance(value, bool):
            accepted = ("yes", "true", "does", "has", "is") if value else (
                "no",
                "false",
                "doesn't",
                "does not",
                "hasn't",
                "has not",
                "isn't",
                "is not",
            )
            return any(token in current_message.casefold() for token in accepted)
        if isinstance(value, (int, float)):
            normalized = " ".join(re.findall(r"[a-z0-9]+", current_message.casefold()))
            return re.search(rf"(?<!\d){re.escape(str(value))}(?!\d)", normalized) is not None
        if isinstance(value, str):
            return self._text_is_grounded(value, current_message)
        if isinstance(value, list):
            return all(
                isinstance(item, str) and self._text_is_grounded(item, current_message)
                for item in value
            )
        return False

    @staticmethod
    def _text_is_grounded(evidence: str, current_message: str) -> bool:
        normalized_evidence = " ".join(re.findall(r"[a-z0-9]+", evidence.casefold()))
        normalized_message = " ".join(
            re.findall(r"[a-z0-9]+", current_message.casefold())
        )
        return bool(normalized_evidence and normalized_evidence in normalized_message)

    @staticmethod
    def _claim_change_index(
        context: ConversationContext,
        proposal: ContextChangeProposal,
        claim: FactClaim,
    ) -> int:
        direct_index = proposal.change_index(claim.claim_id)
        if direct_index is not None:
            return direct_index
        response_ids = {
            response.proposal_id: response.response_id
            for response in proposal.proposal_responses
            if response.action is PendingResolutionAction.SUPPLY
        }
        response_id = next(
            (
                response_ids[pending.proposal_id]
                for pending in context.pending_fact_resolutions
                if pending.claim_id == claim.claim_id
                and pending.proposal_id in response_ids
            ),
            None,
        )
        response_index = proposal.change_index(response_id) if response_id else None
        return response_index if response_index is not None else 0

    def _periodless_direct_binding(
        self,
        context: ConversationContext,
        claim: FactClaim,
        scope_id: str,
    ) -> tuple[str, str, MoneyPeriod] | None:
        subject_id = self._resolve_subject_reference(
            context,
            claim.subject_references[0],
        )
        if subject_id is None:
            return None
        bindings: set[tuple[str, str, MoneyPeriod]] = set()
        if claim.definition_key is not None:
            active = context.active_fact(
                claim.definition_key,
                subject_id,
                scope_id,
            )
            if active is not None and isinstance(active.assertion, PresentAssertion):
                value = active.assertion.value
                if isinstance(value, MoneyFactValue):
                    bindings.add(
                        (claim.definition_key, claim.definition_version, value.period)
                    )
        for proposal in context.pending_fact_resolutions:
            if (
                proposal.scope_id != scope_id
                or proposal.period is None
                or proposal.variable_name is None
                or subject_id not in proposal.referenced_entity_ids
            ):
                continue
            definition = self._registry.find_by_engine_binding(
                proposal.variable_name,
                entity=proposal.variable_entity,
            )
            if definition is not None and (
                claim.definition_key is None
                or definition.key == claim.definition_key
            ):
                bindings.add(
                    (
                        definition.key,
                        definition.version,
                        proposal.period,
                    )
                )
        if len(bindings) != 1:
            return None
        return next(iter(bindings))

    @staticmethod
    def _resolve_subject_reference(
        context: ConversationContext,
        reference: str,
    ) -> str | None:
        normalized = reference.strip().casefold()
        matches = {
            entity.entity_id
            for entity in context.entities
            if normalized
            in {
                entity.entity_id.casefold(),
                (entity.relationship_to_user or "").casefold(),
                *(alias.casefold() for alias in entity.aliases),
            }
        }
        if len(matches) != 1:
            return None
        return next(iter(matches))

    def _grounding_issues(
        self,
        current_message: str,
        proposal: ContextChangeProposal,
    ) -> tuple[ContextValidationIssue, ...]:
        """Check that a model proposal is grounded in the exact current message."""

        claims = proposal.claims
        expressions = self._monetary_parser.extract(current_message)
        message_amounts = {expression.amount for expression in expressions}
        claim_amounts: set[Decimal] = set()
        for claim in claims:
            if isinstance(claim.value, ClaimedMoneyValue):
                claim_amounts.add(claim.value.amount)
            elif isinstance(claim.value, TextFactValue):
                claim_amounts.update(
                    expression.amount
                    for expression in self._monetary_parser.extract(claim.value.value)
                )
            elif isinstance(claim.value, TextSetFactValue):
                claim_amounts.update(
                    expression.amount
                    for value in claim.value.values
                    for expression in self._monetary_parser.extract(value)
                )
        for response in proposal.proposal_responses:
            for update in response.updates:
                if update.path[-1] != "amount":
                    continue
                try:
                    claim_amounts.add(Decimal(str(update.value)))
                except Exception:
                    continue
        current_evidence = " ".join(
            re.findall(r"[a-z0-9]+", current_message.casefold())
        )
        issues = [
            ContextValidationIssue(
                code="uncited_fact_claim",
                path=(
                    "proposal",
                    "changes",
                    str(proposal.change_index(claim.claim_id) or 0),
                    "evidence",
                ),
                claim_index=index,
                message=(
                    "The claim's cited evidence is absent from the exact current "
                    "message. Remove the copied claim or cite current-message text."
                ),
                evidence=claim.evidence,
            )
            for index, claim in enumerate(claims)
            if not self._claim_is_grounded(
                claim,
                current_evidence=current_evidence,
                message_amounts=message_amounts,
            )
        ]
        issues.extend(
            ContextValidationIssue(
                code="missing_monetary_fact_claim",
                path=("proposal", "changes"),
                message=(
                    "The proposal does not preserve this normalized monetary value "
                    "from the current message. Classify it in the same claim list "
                    "without inventing its meaning."
                ),
                evidence=expression.text,
            )
            for expression in expressions
            if expression.amount not in claim_amounts
        )
        issues.extend(
            ContextValidationIssue(
                code="uncited_monetary_fact_claim",
                path=(
                    "proposal",
                    "changes",
                    str(proposal.change_index(claim.claim_id) or 0),
                    "value",
                    "amount",
                ),
                claim_index=index,
                message=(
                    "This monetary claim value is absent from the exact current "
                    "message. Remove the copied or calculated claim."
                ),
                evidence=claim.evidence,
            )
            for index, claim in enumerate(claims)
            if isinstance(claim.value, ClaimedMoneyValue)
            and claim.value.amount not in message_amounts
        )
        return tuple(issues)

    @staticmethod
    def _claim_is_grounded(
        claim: FactClaim,
        *,
        current_evidence: str,
        message_amounts: set[Decimal],
    ) -> bool:
        claim_evidence = " ".join(
            re.findall(r"[a-z0-9]+", claim.evidence.casefold())
        )
        if claim_evidence and claim_evidence in current_evidence:
            return True
        current_tokens = current_evidence.split()
        if (
            current_evidence
            and len(current_tokens) <= 4
            and current_evidence in claim_evidence
        ):
            return True
        value = claim.value
        if isinstance(value, ClaimedMoneyValue):
            return value.amount in message_amounts
        if isinstance(value, IntegerFactValue):
            return re.search(rf"(?<!\d){value.value}(?!\d)", current_evidence) is not None
        if isinstance(value, TextFactValue):
            normalized = " ".join(re.findall(r"[a-z0-9]+", value.value.casefold()))
            return bool(normalized and normalized in current_evidence)
        if isinstance(value, TextSetFactValue):
            normalized_values = (
                " ".join(re.findall(r"[a-z0-9]+", item.casefold()))
                for item in value.values
            )
            return any(
                normalized and normalized in current_evidence
                for normalized in normalized_values
            )
        return False

    @staticmethod
    def _claim_issue(
        index: int,
        claim: FactClaim,
        code: str,
        message: str,
    ) -> ContextValidationIssue:
        return ContextValidationIssue(
            code=code,
            message=message,
            path=("proposal", "changes", str(index)),
            claim_index=index,
            evidence=claim.evidence,
        )

    def _reduce(
        self,
        context: ConversationContext,
        patch: ContextPatch,
        *,
        turn_id: str,
        evidence: str,
    ) -> ContextReduction | tuple[ContextValidationIssue, ...]:
        try:
            return self._reducer.reduce(
                context,
                patch,
                turn_id=turn_id,
                evidence=evidence,
            )
        except (TypeError, ValueError) as exc:
            return (
                ContextValidationIssue(
                    code="context_reduction_failed",
                    message=str(exc),
                    path=("generated_operations",),
                ),
            )

    @classmethod
    def _decision_issues(
        cls,
        decisions: tuple[FactDecision, ...],
    ) -> tuple[ContextValidationIssue, ...]:
        return tuple(
            ContextValidationIssue(
                code=f"context_operation_{decision.status.value}",
                message=decision.reason,
                path=("generated_operations", str(decision.operation_index)),
                operation_index=decision.operation_index,
            )
            for decision in decisions
            if decision.status not in cls._accepted_statuses
        )

    @staticmethod
    def _aggregate_issues(
        context: ConversationContext,
    ) -> tuple[ContextValidationIssue, ...]:
        try:
            ConversationContext.model_validate(context.model_dump(mode="python"))
        except ValidationError as exc:
            return tuple(
                ContextValidationIssue(
                    code="invalid_conversation_context",
                    message=error["msg"],
                    path=tuple(str(item) for item in error["loc"]),
                )
                for error in exc.errors(include_url=False, include_input=False)
            )
        return ()

    @staticmethod
    def _invalid(
        prior: ConversationContext,
        *,
        generated_operations: tuple[ContextOperation, ...] = (),
        claims_to_resolve: tuple[FactClaim, ...] = (),
        decisions: tuple[FactDecision, ...] = (),
        issues: tuple[ContextValidationIssue, ...],
    ) -> ContextValidationOutcome:
        return ContextValidationOutcome(
            status=ContextValidationStatus.NEEDS_CLARIFICATION,
            previous_revision=prior.revision,
            context=prior,
            generated_operations=generated_operations,
            claims_to_resolve=claims_to_resolve,
            decisions=decisions,
            issues=issues,
        )


class ContextChangeApplier:
    """Persist only a fully validated context change with optimistic revision control."""

    def __init__(self, repository: ConversationContextRepository) -> None:
        self._repository = repository

    def apply(self, outcome: ContextValidationOutcome) -> ConversationContext:
        if not outcome.committable:
            raise ValueError("Only a fully validated context change can be applied.")
        return self._repository.save(
            outcome.context,
            expected_revision=outcome.previous_revision,
        )
