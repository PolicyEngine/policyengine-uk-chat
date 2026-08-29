"""Catalogue-backed resolution of declarative monetary fact claims."""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
import json
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from config import DEFAULT_FAST_MODEL, DEFAULT_TEMPERATURE, get_async_client
from conversation_context.change_pipeline import ContextChangeProposal, ContextValidationIssue
from conversation_context.models import (
    AddPendingFactResolutionOperation,
    ClaimedMoneyValue,
    ContextFact,
    ContextOperation,
    ContextPatch,
    ConversationContext,
    EntityKind,
    FactClaim,
    FactClaimRelationship,
    FactResolutionAssignment,
    FactResolutionSupplement,
    FactResolutionStatus,
    FactResolutionTerm,
    MoneyFactValue,
    MoneyPeriod,
    PendingFactResolution,
    PendingFactResolutionResponse,
    PendingResolutionAction,
    PresentAssertion,
    SetFactOperation,
)
from conversation_context.registry import FactDefinitionRegistry, FactValueKind
from tools.contracts import CallerType, Tool, ToolCallContext, ToolSpec, Visibility
from tools.typed_models import SafeToolOutput


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MappingConfidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class MappingStatus(str, Enum):
    MATCHED = "matched"
    AMBIGUOUS = "ambiguous"
    UNSUPPORTED = "unsupported"


class PolicyEngineVariableCandidate(StrictModel):
    name: str
    label: str | None = None
    entity: str
    description: str | None = None
    definition_period: str | None = None
    value_type: str | None = None


class VariableMappingSelection(StrictModel):
    status: MappingStatus
    variable_name: str | None = None
    confidence: MappingConfidence = MappingConfidence.LOW
    target_period: MoneyPeriod | None = None


class VariableMappingUsage(StrictModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    def plus(self, other: "VariableMappingUsage") -> "VariableMappingUsage":
        return VariableMappingUsage(
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


class VariableMappingResult(StrictModel):
    selection: VariableMappingSelection
    usage: VariableMappingUsage = Field(default_factory=VariableMappingUsage)


class VariableMapper(Protocol):
    async def select(
        self,
        *,
        claim: FactClaim,
        candidates: tuple[PolicyEngineVariableCandidate, ...],
        context: ConversationContext,
        registry: FactDefinitionRegistry,
        validation_issues: tuple[ContextValidationIssue, ...] = (),
    ) -> VariableMappingResult: ...


class AnthropicVariableMapper:
    """Select one exact returned variable without calculating assignments."""

    async def select(
        self,
        *,
        claim: FactClaim,
        candidates: tuple[PolicyEngineVariableCandidate, ...],
        context: ConversationContext,
        registry: FactDefinitionRegistry,
        validation_issues: tuple[ContextValidationIssue, ...] = (),
    ) -> VariableMappingResult:
        client = get_async_client()  # type: ignore[no-untyped-call]
        tool = {
            "name": "submit_variable_mapping",
            "description": "Select at most one exact returned PolicyEngine variable.",
            "input_schema": VariableMappingSelection.model_json_schema(),
        }
        active_bindings = []
        for fact in context.active_facts():
            try:
                definition = registry.get(
                    fact.definition_key,
                    fact.definition_version,
                )
            except KeyError:
                continue
            if definition.engine_binding is None:
                continue
            active_bindings.append(
                {
                    "definition_key": definition.key,
                    "engine_binding": definition.engine_binding,
                    "subject_entity_id": fact.subject_entity_id,
                    "assertion": fact.assertion.model_dump(mode="json"),
                }
            )
        system = (
            "Map the cited claim to at most one PolicyEngine variable from the exact "
            "candidate list. Never invent or alter a variable name. Use the current "
            "active engine bindings to interpret a concise continuation. Mark confidence "
            "high only when the concept, entity, value type, and relationship align; "
            "otherwise return ambiguous or unsupported. Select a target period only "
            "when it follows from the returned variable definition period, an explicit "
            "claim period, or a compatible active binding. Do not calculate, allocate, "
            "apply defaults, or write a user-facing answer."
        )
        payload = {
            "claim": claim.model_dump(mode="json"),
            "candidates": [item.model_dump(mode="json") for item in candidates],
            "entities": [item.model_dump(mode="json") for item in context.entities],
            "active_engine_bindings": active_bindings,
            "validation_issues": [
                item.model_dump(mode="json") for item in validation_issues
            ],
        }
        last_error: Exception | None = None
        for _attempt in range(2):
            response = await client.messages.create(
                model=DEFAULT_FAST_MODEL,
                max_tokens=500,
                temperature=DEFAULT_TEMPERATURE,
                system=system,
                messages=[
                    {
                        "role": "user",
                        "content": json.dumps(payload, separators=(",", ":")),
                    }
                ],
                tools=[tool],
                tool_choice={"type": "tool", "name": "submit_variable_mapping"},
            )
            block = next(
                (
                    item
                    for item in response.content
                    if getattr(item, "type", None) == "tool_use"
                    and getattr(item, "name", None) == "submit_variable_mapping"
                ),
                None,
            )
            if block is None:
                last_error = RuntimeError(
                    "Variable mapper did not return structured output."
                )
                continue
            try:
                selection = VariableMappingSelection.model_validate(block.input)
            except ValidationError as exc:
                last_error = exc
                continue
            usage = getattr(response, "usage", None)
            return VariableMappingResult(
                selection=selection,
                usage=VariableMappingUsage(
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
                ),
            )
        raise RuntimeError(
            "Variable mapper failed to return a valid selection after one retry."
        ) from last_error


class FactResolutionDecision(StrictModel):
    claim_id: str
    status: FactResolutionStatus | Literal["resolved"]
    candidates: tuple[PolicyEngineVariableCandidate, ...] = ()
    selection_source: Literal["proposal", "resolver_model"]
    selection: VariableMappingSelection
    proposal: PendingFactResolution | None = None
    operation: SetFactOperation | None = None

    @model_validator(mode="after")
    def validate_result(self) -> "FactResolutionDecision":
        if self.status == "resolved":
            if self.operation is None or self.proposal is not None:
                raise ValueError(
                    "a resolved direct fact requires one operation and no pending proposal"
                )
        elif self.proposal is None or self.operation is not None:
            raise ValueError(
                "an incomplete or confirmable resolution requires one pending proposal"
            )
        return self


class ResolveContextChangeInput(StrictModel):
    context: ConversationContext
    proposal: ContextChangeProposal | None = None
    validation_issues: tuple[ContextValidationIssue, ...] = ()
    claims: tuple[FactClaim, ...]
    turn_id: str
    evidence: str

    @model_validator(mode="after")
    def validate_monetary_claims(self) -> "ResolveContextChangeInput":
        if any(not isinstance(claim.value, ClaimedMoneyValue) for claim in self.claims):
            raise ValueError(
                "catalogue-backed fact resolution accepts only monetary fact claims"
            )
        if self.proposal is not None:
            proposal_claim_ids = {claim.claim_id for claim in self.proposal.claims}
            supplied_proposal_ids = {
                response.proposal_id
                for response in self.proposal.proposal_responses
                if response.action is PendingResolutionAction.SUPPLY
            }
            proposal_claim_ids.update(
                pending.source_claim.claim_id
                for pending in self.context.pending_fact_resolutions
                if pending.proposal_id in supplied_proposal_ids
                and pending.source_claim is not None
            )
            if any(claim.claim_id not in proposal_claim_ids for claim in self.claims):
                raise ValueError(
                    "every claim requiring resolution must come from the current "
                    "proposal or a referenced retained source claim"
                )
        return self


class ResolveContextChangeOutput(StrictModel):
    patch: ContextPatch
    decisions: tuple[FactResolutionDecision, ...]
    usage: VariableMappingUsage = Field(default_factory=VariableMappingUsage)


class FactConstraintIssue(str, Enum):
    SUBJECT_REQUIRED = "subject_required"
    ALLOCATION_REQUIRED = "allocation_required"
    INCONSISTENT_TOTAL = "inconsistent_total"


class FactConstraintSolution(StrictModel):
    terms: tuple[FactResolutionTerm, ...]
    subject_entity_id: str | None = None
    amount: Decimal | None = None
    issue: FactConstraintIssue | None = None


def _solve_constraint(
    *,
    variable_name: str,
    relationship: FactClaimRelationship,
    entity_ids: tuple[str, ...],
    known_values: dict[str, Decimal | None],
    expected_total: Decimal,
) -> FactConstraintSolution:
    terms = tuple(
        FactResolutionTerm(
            variable_name=variable_name,
            subject_entity_id=entity_id,
            known_value=known_values.get(entity_id),
        )
        for entity_id in entity_ids
    )
    if relationship is FactClaimRelationship.DIRECT:
        if len(entity_ids) != 1:
            return FactConstraintSolution(
                terms=terms,
                issue=FactConstraintIssue.SUBJECT_REQUIRED,
            )
        return FactConstraintSolution(
            terms=terms,
            subject_entity_id=entity_ids[0],
            amount=expected_total,
        )

    unknown = tuple(
        entity_id
        for entity_id in entity_ids
        if known_values.get(entity_id) is None
    )
    if len(unknown) != 1:
        return FactConstraintSolution(
            terms=terms,
            issue=FactConstraintIssue.ALLOCATION_REQUIRED,
        )
    known_total = sum(
        (
            known
            for known in known_values.values()
            if known is not None
        ),
        start=Decimal("0"),
    )
    amount = expected_total - known_total
    if amount < 0:
        return FactConstraintSolution(
            terms=terms,
            issue=FactConstraintIssue.INCONSISTENT_TOTAL,
        )
    return FactConstraintSolution(
        terms=terms,
        subject_entity_id=unknown[0],
        amount=amount,
    )


class ContextChangeResolver:
    """Resolve model-proposed claims using model selection and checked calculations."""

    _periods_per_year = {
        MoneyPeriod.ANNUAL: Decimal("1"),
        MoneyPeriod.MONTHLY: Decimal("12"),
        MoneyPeriod.FOUR_WEEKLY: Decimal("13"),
        MoneyPeriod.WEEKLY: Decimal("52"),
    }

    def __init__(
        self,
        registry: FactDefinitionRegistry,
        mapper: VariableMapper,
    ) -> None:
        self._registry = registry
        self._mapper = mapper

    async def resolve(
        self,
        tool_input: "ResolveContextChangeInput",
        candidate_sets: tuple[tuple[PolicyEngineVariableCandidate, ...], ...],
    ) -> "ResolveContextChangeOutput":
        if len(candidate_sets) != len(tool_input.claims):
            raise ValueError("Each unresolved claim requires one candidate set.")
        decisions: list[FactResolutionDecision] = []
        operations: list[ContextOperation] = []
        usage = VariableMappingUsage()
        for claim, candidates in zip(tool_input.claims, candidate_sets, strict=True):
            selection = self._proposal_selection(claim, candidates)
            if selection is None:
                mapped = await self._mapper.select(
                    claim=claim,
                    candidates=candidates,
                    context=tool_input.context,
                    registry=self._registry,
                    validation_issues=tool_input.validation_issues,
                )
                selection = mapped.selection
                selection_source: Literal["proposal", "resolver_model"] = (
                    "resolver_model"
                )
                usage = usage.plus(mapped.usage)
            else:
                selection_source = "proposal"
            resolution = self._resolve_claim(
                claim=claim,
                selection=selection,
                candidates=candidates,
                context=tool_input.context,
                context_proposal=tool_input.proposal,
                turn_id=tool_input.turn_id,
                evidence=tool_input.evidence,
            )
            if isinstance(resolution, SetFactOperation):
                decisions.append(
                    FactResolutionDecision(
                        claim_id=claim.claim_id,
                        status="resolved",
                        candidates=candidates,
                        selection_source=selection_source,
                        selection=selection,
                        operation=resolution,
                    )
                )
                operations.append(resolution)
            else:
                decisions.append(
                    FactResolutionDecision(
                        claim_id=claim.claim_id,
                        status=resolution.status,
                        candidates=candidates,
                        selection_source=selection_source,
                        selection=selection,
                        proposal=resolution,
                    )
                )
                operations.append(
                    AddPendingFactResolutionOperation(proposal=resolution)
                )
        return ResolveContextChangeOutput(
            patch=ContextPatch(
                expected_revision=tool_input.context.revision,
                operations=tuple(operations),
            ),
            decisions=tuple(decisions),
            usage=usage,
        )

    def _proposal_selection(
        self,
        claim: FactClaim,
        candidates: tuple[PolicyEngineVariableCandidate, ...],
    ) -> VariableMappingSelection | None:
        """Validate a semantic mapping already selected by the proposal model."""

        if claim.definition_key is None:
            return None
        try:
            definition = self._registry.get(
                claim.definition_key,
                claim.definition_version,
            )
        except KeyError:
            return None
        if definition.engine_binding is None:
            return None
        binding_parts = definition.engine_binding.split(".", 1)
        binding_entity = binding_parts[0] if len(binding_parts) == 2 else None
        binding_name = binding_parts[-1]
        selected = next(
            (
                candidate
                for candidate in candidates
                if candidate.name == binding_name
                and binding_entity in {None, candidate.entity}
            ),
            None,
        )
        if selected is None:
            return None
        return VariableMappingSelection(
            status=MappingStatus.MATCHED,
            variable_name=selected.name,
            confidence=MappingConfidence.HIGH,
            target_period=claim.value.period
            if isinstance(claim.value, ClaimedMoneyValue)
            else None,
        )

    def _resolve_claim(
        self,
        *,
        claim: FactClaim,
        selection: VariableMappingSelection,
        candidates: tuple[PolicyEngineVariableCandidate, ...],
        context: ConversationContext,
        context_proposal: ContextChangeProposal | None,
        turn_id: str,
        evidence: str,
    ) -> PendingFactResolution | SetFactOperation:
        claim_value = self._claim_value(claim)
        scope_id = context.focus.scope_id or next(
            scope.scope_id for scope in context.scopes if scope.active
        )
        entity_ids = self._resolve_entities(claim.subject_references, context)
        prior_resolution = self._supplemented_resolution(
            claim,
            context=context,
            proposal=context_proposal,
        )
        if prior_resolution is None:
            source_turn_id = turn_id
            source_claim = claim
            supplements: tuple[FactResolutionSupplement, ...] = ()
            source_evidence = evidence
            created_revision = context.revision
        else:
            prior, response = prior_resolution
            source_turn_id = prior.source_turn_id
            source_claim = prior.source_claim or claim
            supplements = (
                *prior.supplements,
                FactResolutionSupplement(
                    turn_id=turn_id,
                    evidence=response.evidence,
                    updates=response.updates,
                ),
            )
            source_evidence = prior.evidence
            created_revision = prior.created_revision
        base = {
            "claim_id": claim.claim_id,
            "source_turn_id": source_turn_id,
            "source_claim": source_claim,
            "supplements": supplements,
            "scope_id": scope_id,
            "referenced_entity_ids": entity_ids,
            "evidence": source_evidence,
            "relationship": claim.relationship,
            "mapping_confidence": selection.confidence.value,
            # Preserve the claim's native constraint even when catalogue mapping
            # needs clarification. Later explicit facts can then satisfy it without
            # depending on another model interpretation of the original message.
            "expected_total": claim_value.amount,
            "period": claim_value.period,
            "created_revision": created_revision,
        }
        if len(entity_ids) != len(claim.subject_references):
            return PendingFactResolution.model_validate(
                {
                    **base,
                    "status": FactResolutionStatus.NEEDS_CLARIFICATION,
                    "prompt": (
                        "Which exact people or household should the stated value "
                        "cover?"
                    ),
                }
            )
        selected = next(
            (
                item
                for item in candidates
                if item.name == selection.variable_name
            ),
            None,
        )
        if (
            selection.status is not MappingStatus.MATCHED
            or selection.confidence is not MappingConfidence.HIGH
            or selected is None
        ):
            return PendingFactResolution.model_validate(
                {
                    **base,
                    "status": FactResolutionStatus.NEEDS_CLARIFICATION,
                    "prompt": (
                        f"What does the {self._money(claim_value.amount)}"
                        f"{self._claim_period_suffix(claim_value.period)} amount "
                        "represent, and which household member or members does it "
                        "cover?"
                    ),
                }
            )

        value_type = (selected.value_type or "").casefold()
        if value_type and not any(
            token in value_type for token in ("float", "int", "decimal", "number")
        ):
            return PendingFactResolution.model_validate(
                {
                    **base,
                    "status": FactResolutionStatus.NEEDS_CLARIFICATION,
                    "prompt": (
                        f"{self._plain_label(selected)} does not accept a monetary "
                        "amount. What kind of income or expense did you mean?"
                    ),
                    "variable_name": selected.name,
                    "variable_entity": selected.entity,
                    "variable_label": selected.label,
                    "definition_period": selected.definition_period,
                }
            )

        variable_period = self._metadata_period(selected.definition_period)
        target_period = variable_period or selection.target_period or claim_value.period
        if target_period is None:
            return PendingFactResolution.model_validate(
                {
                    **base,
                    "status": FactResolutionStatus.NEEDS_CLARIFICATION,
                    "prompt": (
                    f"What period should I use for the {claim.concept} amount: "
                    "weekly, every four weeks, monthly, or annual?"
                    ),
                    "variable_name": selected.name,
                    "variable_entity": selected.entity,
                    "variable_label": selected.label,
                    "definition_period": selected.definition_period,
                }
            )
        if not self._entity_matches(selected.entity, entity_ids, context):
            return PendingFactResolution.model_validate(
                {
                    **base,
                    "status": FactResolutionStatus.NEEDS_CLARIFICATION,
                    "prompt": (
                        f"Which person or household should the "
                        f"{self._plain_label(selected).casefold()} amount apply to?"
                    ),
                    "variable_name": selected.name,
                    "variable_entity": selected.entity,
                    "variable_label": selected.label,
                    "definition_period": selected.definition_period,
                    "period": target_period,
                }
            )
        definition = self._registry.ensure_engine_definition(
            variable_name=selected.name,
            entity=selected.entity,
            label=selected.label or selected.name.replace("_", " ").title(),
            value_kind=FactValueKind.MONEY,
        )
        if definition.value_kind is not FactValueKind.MONEY:
            return PendingFactResolution.model_validate(
                {
                    **base,
                    "status": FactResolutionStatus.NEEDS_CLARIFICATION,
                    "prompt": (
                        f"{self._plain_label(selected)} does not accept a monetary "
                        "amount. What kind of income or expense did you mean?"
                    ),
                    "variable_name": selected.name,
                    "variable_entity": selected.entity,
                    "variable_label": selected.label,
                    "definition_period": selected.definition_period,
                    "period": target_period,
                }
            )
        expected_total = self._convert(
            claim_value.amount,
            claim_value.period or target_period,
            target_period,
        )
        if (
            claim.relationship is FactClaimRelationship.DIRECT
            and len(entity_ids) == 1
            and claim_value.period in {None, target_period}
        ):
            return SetFactOperation(
                definition_key=definition.key,
                definition_version=definition.version,
                subject_reference=entity_ids[0],
                scope_id=scope_id,
                assertion=PresentAssertion(
                    value=MoneyFactValue(
                        amount=expected_total,
                        period=target_period,
                        currency=claim_value.currency,
                    )
                ),
                correction=claim.correction,
            )
        known_values: dict[str, Decimal | None] = {}
        for entity_id in entity_ids:
            fact = context.active_fact(definition.key, entity_id, scope_id)
            known = self._money_value(fact, target_period)
            known_values[entity_id] = known
        solution = _solve_constraint(
            variable_name=selected.name,
            relationship=claim.relationship,
            entity_ids=entity_ids,
            known_values=known_values,
            expected_total=expected_total,
        )
        if solution.issue is not None:
            prompt = {
                FactConstraintIssue.SUBJECT_REQUIRED: (
                    "Which one person or household should receive this value?"
                ),
                FactConstraintIssue.ALLOCATION_REQUIRED: (
                    f"How should the {self._money(expected_total)} "
                    f"{self._period_label(target_period)} total be divided between "
                    "the household members?"
                ),
                FactConstraintIssue.INCONSISTENT_TOTAL: (
                    f"The known {selected.label or selected.name} amounts exceed "
                    f"the stated total of {self._money(expected_total)}. Which "
                    "amounts should I use?"
                ),
            }[solution.issue]
            return self._clarification(
                base,
                selected,
                target_period,
                expected_total,
                solution.terms,
                prompt,
            )
        if solution.subject_entity_id is None or solution.amount is None:
            raise RuntimeError("A solved fact constraint lacks its exact assignment.")

        subject_id = solution.subject_entity_id
        assignment_amount = solution.amount
        assignment = FactResolutionAssignment(
            definition_key=definition.key,
            subject_entity_id=subject_id,
            scope_id=scope_id,
            assertion=PresentAssertion(
                value=MoneyFactValue(
                    amount=assignment_amount,
                    period=target_period,
                    currency=claim_value.currency,
                )
            ),
            correction=claim.correction,
        )
        subject = self._entity_label(subject_id, context)
        prompt = (
            f"Using the amounts already provided, the "
            f"{self._money(expected_total)} {self._period_label(target_period)} "
            f"{self._plain_label(selected).casefold()} total implies "
            f"{self._money(assignment_amount)} {self._period_label(target_period)} "
            f"for {subject}. "
            "Is that the correct breakdown?"
        )
        return PendingFactResolution.model_validate(
            {
                **base,
                "status": FactResolutionStatus.AWAITING_CONFIRMATION,
                "prompt": prompt,
                "variable_name": selected.name,
                "variable_entity": selected.entity,
                "variable_label": selected.label,
                "definition_period": selected.definition_period,
                "expected_total": expected_total,
                "period": target_period,
                "terms": solution.terms,
                "assignments": (assignment,),
            }
        )

    @staticmethod
    def _supplemented_resolution(
        claim: FactClaim,
        *,
        context: ConversationContext,
        proposal: ContextChangeProposal | None,
    ) -> tuple[PendingFactResolution, PendingFactResolutionResponse] | None:
        if proposal is None:
            return None
        responses = {
            response.proposal_id: response
            for response in proposal.proposal_responses
            if response.action is PendingResolutionAction.SUPPLY
        }
        return next(
            (
                (pending, responses[pending.proposal_id])
                for pending in context.pending_fact_resolutions
                if pending.claim_id == claim.claim_id
                and pending.proposal_id in responses
            ),
            None,
        )

    @staticmethod
    def _clarification(
        base: dict[str, object],
        selected: PolicyEngineVariableCandidate,
        period: MoneyPeriod,
        expected_total: Decimal,
        terms: tuple[FactResolutionTerm, ...],
        prompt: str,
    ) -> PendingFactResolution:
        return PendingFactResolution.model_validate(
            {
                **base,
                "status": FactResolutionStatus.NEEDS_CLARIFICATION,
                "prompt": prompt,
                "variable_name": selected.name,
                "variable_entity": selected.entity,
                "variable_label": selected.label,
                "definition_period": selected.definition_period,
                "expected_total": expected_total,
                "period": period,
                "terms": terms,
            }
        )

    @staticmethod
    def _resolve_entities(
        references: tuple[str, ...],
        context: ConversationContext,
    ) -> tuple[str, ...]:
        aliases: dict[str, str] = {}
        for entity in context.entities:
            aliases[entity.entity_id.casefold()] = entity.entity_id
            if entity.relationship_to_user:
                aliases[entity.relationship_to_user.casefold()] = entity.entity_id
            for alias in entity.aliases:
                aliases[alias.casefold()] = entity.entity_id
        resolved = tuple(
            dict.fromkeys(
                aliases[reference.casefold()]
                for reference in references
                if reference.casefold() in aliases
            )
        )
        return resolved

    @staticmethod
    def _entity_matches(
        variable_entity: str,
        entity_ids: tuple[str, ...],
        context: ConversationContext,
    ) -> bool:
        expected = {
            "person": EntityKind.PERSON,
            "household": EntityKind.HOUSEHOLD,
            "benunit": EntityKind.HOUSEHOLD,
        }.get(variable_entity)
        if expected is None or not entity_ids:
            return False
        return all(
            next(item for item in context.entities if item.entity_id == entity_id).kind
            is expected
            for entity_id in entity_ids
        )

    @classmethod
    def _convert(
        cls,
        amount: Decimal,
        source: MoneyPeriod,
        target: MoneyPeriod,
    ) -> Decimal:
        annual = amount * cls._periods_per_year[source]
        return annual / cls._periods_per_year[target]

    @classmethod
    def _money_value(
        cls,
        fact: ContextFact | None,
        period: MoneyPeriod,
    ) -> Decimal | None:
        if fact is None or not isinstance(fact.assertion, PresentAssertion):
            return None
        value = fact.assertion.value
        if not isinstance(value, MoneyFactValue):
            return None
        return cls._convert(value.amount, value.period, period)

    @staticmethod
    def _metadata_period(value: str | None) -> MoneyPeriod | None:
        normalized = (value or "").casefold()
        return {
            "year": MoneyPeriod.ANNUAL,
            "annual": MoneyPeriod.ANNUAL,
            "month": MoneyPeriod.MONTHLY,
            "monthly": MoneyPeriod.MONTHLY,
            "week": MoneyPeriod.WEEKLY,
            "weekly": MoneyPeriod.WEEKLY,
        }.get(normalized)

    @staticmethod
    def _claim_value(claim: FactClaim) -> ClaimedMoneyValue:
        if not isinstance(claim.value, ClaimedMoneyValue):
            raise ValueError("fact resolution requires a monetary fact claim")
        return claim.value

    @staticmethod
    def _plain_label(candidate: PolicyEngineVariableCandidate) -> str:
        return (
            candidate.label
            or candidate.name.replace("_", " ").replace("-", " ")
        ).strip()

    @staticmethod
    def _claim_period_suffix(period: MoneyPeriod | None) -> str:
        if period is None:
            return ""
        return " " + {
            MoneyPeriod.ANNUAL: "annual",
            MoneyPeriod.MONTHLY: "monthly",
            MoneyPeriod.FOUR_WEEKLY: "four-weekly",
            MoneyPeriod.WEEKLY: "weekly",
        }[period]

    @staticmethod
    def _entity_label(entity_id: str, context: ConversationContext) -> str:
        entity = next(item for item in context.entities if item.entity_id == entity_id)
        if entity.relationship_to_user == "self":
            return "you"
        if entity.relationship_to_user:
            return f"your {entity.relationship_to_user.replace('_', ' ')}"
        return "the other household member"

    @staticmethod
    def _money(value: Decimal) -> str:
        return f"£{value:,.2f}".replace(".00", "")

    @staticmethod
    def _period_label(period: MoneyPeriod) -> str:
        return {
            MoneyPeriod.ANNUAL: "per year",
            MoneyPeriod.MONTHLY: "per month",
            MoneyPeriod.FOUR_WEEKLY: "every four weeks",
            MoneyPeriod.WEEKLY: "per week",
        }[period]


class ResolveContextChangeTool(
    Tool["ResolveContextChangeInput", "ResolveContextChangeOutput"]
):
    spec = ToolSpec(
        identifier="resolve_context_change",
        version="1",
        description=(
            "Map monetary fact claims to exact PolicyEngine variables and propose "
            "only validated, confirmable assignments."
        ),
        visibility=Visibility.PRIVATE,
        allowed_callers=frozenset({CallerType.RUNTIME}),
        input_model=ResolveContextChangeInput,
        output_model=ResolveContextChangeOutput,
        tool_dependencies=("search_variables",),
    )

    def __init__(self, resolver: ContextChangeResolver) -> None:
        self._resolver = resolver

    async def run(
        self,
        tool_input: "ResolveContextChangeInput",
        context: ToolCallContext,
    ) -> "ResolveContextChangeOutput":
        candidate_sets: list[tuple[PolicyEngineVariableCandidate, ...]] = []
        for claim in tool_input.claims:
            raw = await context.invoke_tool(
                "search_variables",
                {"query": claim.concept, "limit": 12},
            )
            rows = raw.root.get("variables") if isinstance(raw, SafeToolOutput) else None
            if not isinstance(rows, list):
                rows = []
            candidates: list[PolicyEngineVariableCandidate] = []
            for row in rows or []:
                if not isinstance(row, dict):
                    continue
                try:
                    candidates.append(
                        PolicyEngineVariableCandidate.model_validate(
                            {
                                "name": row.get("name"),
                                "label": row.get("label"),
                                "entity": row.get("entity"),
                                "description": row.get("description"),
                                "definition_period": row.get("definition_period"),
                                "value_type": row.get("value_type"),
                            }
                        )
                    )
                except ValidationError:
                    continue
            candidate_sets.append(tuple(candidates))
        result = await self._resolver.resolve(tool_input, tuple(candidate_sets))
        context.record_model_usage(**result.usage.model_dump())
        return result
