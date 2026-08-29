"""Household input assembly, explicit assumptions, validation, and calculation."""

from __future__ import annotations

import hashlib
import json
import re
from enum import Enum
from typing import Literal

from pydantic import AliasChoices, Field, JsonValue

from capabilities.artifacts import (
    AggregateDimension,
    AggregateValue,
    ArtifactProvenance,
    HouseholdRef,
    HouseholdEntityPosition,
    HouseholdResultRef,
    HouseholdValue,
    PolicyScenarioRef,
)
from capabilities.contracts import (
    ArtifactContract,
    Capability,
    CapabilityDependency,
    CapabilitySpec,
    Completed,
    Failed,
    NeedsInput,
    Unsupported,
)
from capabilities.input_resolution import InputSource, resolve_policy_year
from capabilities.household_input import (
    AmountFrequency,
    HouseholdEvidence,
    HouseholdEvidenceAmbiguity,
    HouseholdEvidenceAmbiguityKind,
    HouseholdEvidenceAssembler,
    HouseholdEvidenceResult,
    HouseholdInvocationDefaults,
    HouseholdCalculationRequirements,
    HouseholdInputResolver,
    HouseholdInputCompleteness,
    PeriodicAmount,
    PersonEvidence,
    StrictModel,
)
from capabilities.policy_reform import PolicyReformOutput
from conversation_context.household_view import HouseholdContextView
from conversation_context.engine_projection import (
    HouseholdEngineFactProjector,
    HouseholdEngineInputs,
)
from conversation_context.models import (
    CapabilityInvocationReference,
    ExplicitAbsenceAssertion,
    PendingQuestionStatus,
    FactRequirement,
)
from tools.analysis_support import (
    ExtractResultFindingsOutput,
    NumericalFact,
)
from tools.contracts import CallerType, Tool, ToolCallContext, ToolSpec, Visibility
from tools.typed_models import SafeToolOutput


class HouseholdAssemblyStatus(str, Enum):
    READY = "ready"
    NEEDS_INPUT = "needs_input"
    FAILED = "failed"


class HouseholdAssumption(StrictModel):
    field_id: str
    label: str
    assumed_value: JsonValue
    plain_statement: str
    label_source: Literal["catalogue", "household_contract", "system_default"]
    material: bool = True


class HouseholdCandidate(StrictModel):
    people: tuple[dict[str, JsonValue], ...]
    benunit: dict[str, JsonValue]
    household: dict[str, JsonValue]
    field_values: tuple[HouseholdValue, ...]
    entity_positions: tuple[HouseholdEntityPosition, ...] = ()
    input_narration_facts: tuple[NumericalFact, ...] = ()


class AssembleHouseholdInput(StrictModel):
    description: str
    requirements: HouseholdCalculationRequirements = Field(
        default_factory=HouseholdCalculationRequirements
    )
    existing_values: tuple[HouseholdValue, ...] = ()
    retained_evidence: HouseholdEvidence = Field(default_factory=HouseholdEvidence)
    invocation_defaults: HouseholdInvocationDefaults = Field(
        default_factory=HouseholdInvocationDefaults
    )
    retained_ambiguities: tuple[HouseholdEvidenceAmbiguity, ...] = ()
    pending_fields: tuple[str, ...] = ()


class AssembleHouseholdOutput(StrictModel):
    status: HouseholdAssemblyStatus
    evidence: HouseholdEvidence = Field(default_factory=HouseholdEvidence)
    invocation_defaults: HouseholdInvocationDefaults = Field(
        default_factory=HouseholdInvocationDefaults
    )
    ambiguities: tuple[HouseholdEvidenceAmbiguity, ...] = ()
    candidate: HouseholdCandidate | None = None
    assumptions: tuple[HouseholdAssumption, ...] = ()
    questions: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()
    fact_requirements: tuple[FactRequirement, ...] = ()
    error: str | None = None


_CATALOGUE_FIELDS = {
    "age": "Age",
    "employment_income": "Employment income",
    "self_employment_income": "Self-employment income",
    "pension_income": "Pension income",
    "is_married": "Married or in a civil partnership",
    "childcare_expenses": "Childcare expenses",
    "rent": "Rent",
    "council_tax": "Council Tax",
}

_HOUSEHOLD_OUTPUT_ALIASES = {
    "benefits": "household_benefits",
    "benefit entitlement": "household_benefits",
    "benefit entitlements": "household_benefits",
    "household benefits": "household_benefits",
    "housing benefit": "housing_benefit",
    "income tax": "income_tax",
    "national insurance": "national_insurance",
    "national insurance contribution": "national_insurance",
    "national insurance contributions": "national_insurance",
    "total tax": "household_tax",
    "total taxes": "household_tax",
    "household tax": "household_tax",
    "net income": "household_net_income",
    "household net income": "household_net_income",
    "universal credit": "universal_credit",
}

_TAX_ONLY_HOUSEHOLD_OUTPUTS = frozenset(
    {"income_tax", "national_insurance", "household_tax"}
)

_HOUSEHOLD_OUTPUT_GROUPS = {
    "tax": (
        "income_tax",
        "national_insurance",
        "household_tax",
    ),
    "taxes": (
        "income_tax",
        "national_insurance",
        "household_tax",
    ),
}


class AssembleHouseholdCandidateTool(
    Tool[AssembleHouseholdInput, AssembleHouseholdOutput]
):
    spec = ToolSpec(
        identifier="assemble_household_candidate",
        version="1",
        description=(
            "Extract household evidence, apply documented safe defaults, and return "
            "every material assumption with a plain-language label."
        ),
        visibility=Visibility.PRIVATE,
        allowed_callers=frozenset({CallerType.CAPABILITY}),
        input_model=AssembleHouseholdInput,
        output_model=AssembleHouseholdOutput,
        tool_dependencies=("get_variable",),
    )

    def __init__(
        self,
        assembler: HouseholdEvidenceAssembler | None = None,
        resolver: HouseholdInputResolver | None = None,
    ) -> None:
        self._assembler = assembler
        self._resolver = resolver or HouseholdInputResolver()

    async def run(self, tool_input: AssembleHouseholdInput, context: ToolCallContext):
        assembled = (
            await self._assembler.assemble(
                description=tool_input.description,
                retained_evidence=tool_input.retained_evidence,
                retained_ambiguities=tool_input.retained_ambiguities,
            )
            if self._assembler is not None
            else HouseholdEvidenceResult(evidence=HouseholdEvidence())
        )
        context.record_model_usage(**assembled.usage.model_dump())
        artifact_evidence = self._evidence_from_values(tool_input.existing_values)
        defaulted_evidence = self._merge_evidence(
            tool_input.invocation_defaults.evidence,
            artifact_evidence,
        )
        retained_evidence = self._merge_evidence(
            defaulted_evidence,
            tool_input.retained_evidence,
        )
        current_evidence = self._as_current_user_evidence(
            assembled.evidence,
            assembled.ambiguities,
        )
        evidence = self._merge_evidence(retained_evidence, current_evidence)
        ambiguities = self._merge_ambiguities(
            tool_input.retained_ambiguities,
            assembled.ambiguities,
            current_evidence,
        )
        evidence, ambiguities = self._resolver.apply_documented_defaults(
            evidence,
            ambiguities,
            current_user_message=tool_input.description,
            requirements=tool_input.requirements,
        )
        invocation_defaults = HouseholdInvocationDefaults(
            evidence=self._merge_evidence(
                tool_input.invocation_defaults.evidence,
                self._default_only_evidence(evidence),
            )
        )
        resolution = self._resolver.resolve(
            evidence,
            ambiguities,
            tool_input.requirements,
        )
        if resolution.questions:
            return AssembleHouseholdOutput(
                status=HouseholdAssemblyStatus.NEEDS_INPUT,
                evidence=evidence,
                invocation_defaults=invocation_defaults,
                ambiguities=ambiguities,
                questions=resolution.questions,
                missing_fields=resolution.missing_fields,
                fact_requirements=resolution.fact_requirements,
            )

        labels = await self._catalogue_labels(context)
        if labels is None:
            return AssembleHouseholdOutput(
                status=HouseholdAssemblyStatus.FAILED,
                evidence=evidence,
                invocation_defaults=invocation_defaults,
                ambiguities=ambiguities,
                error="A supported household input lacks an authoritative presentation label.",
            )
        assumptions: list[HouseholdAssumption] = []
        values: list[HouseholdValue] = []
        adult_count = sum(
            1
            for person in evidence.people
            if person.age is not None and person.age >= 16
        )
        child_count = sum(
            1
            for person in evidence.people
            if person.age is not None and person.age < 16
        )
        input_facts: list[NumericalFact] = [
            NumericalFact(
                label="Number of people in the household",
                value=len(evidence.people),
                unit="people",
            ),
            NumericalFact(
                label="Number of adults in the household",
                value=adult_count,
                unit="adults",
            ),
            NumericalFact(
                label="Number of children in the household",
                value=child_count,
                unit="children",
            ),
        ]
        people: list[dict[str, JsonValue]] = []
        for index, person in enumerate(evidence.people):
            person_data: dict[str, JsonValue] = {"age": person.age}
            if person.age is not None:
                input_facts.append(
                    NumericalFact(
                        label=(
                            "Your age"
                            if person.relationship_to_user == "self"
                            else (
                                f"Age of {person.display_label}"
                                if person.display_label
                                else "Age"
                            )
                        ),
                        value=person.age,
                        unit="years",
                    )
                )
            values.append(
                HouseholdValue(
                    field_id=f"people[{index}].age",
                    label=labels["age"],
                    value=person.age,
                    source=person.sources.get("age", "user"),
                    subject_entity_id=person.entity_id,
                    engine_variable="age",
                )
            )
            for field in (
                "employment_income",
                "self_employment_income",
                "pension_income",
            ):
                periodic_value = getattr(person, field)
                if periodic_value is None:
                    annual_value = 0.0
                    assumptions.append(
                        HouseholdAssumption(
                            field_id=f"people[{index}].{field}",
                            label=labels[field],
                            assumed_value=annual_value,
                            plain_statement=(
                                f"No {labels[field].casefold()}."
                                if len(evidence.people) == 1
                                else (
                                    f"{self._person_subject(person, index)} has no "
                                    f"{labels[field].casefold()}."
                                )
                            ),
                            label_source="catalogue",
                        )
                    )
                    source = "default"
                else:
                    annual_value = periodic_value.annual_value()
                    input_facts.extend(
                        self._periodic_amount_facts(
                            labels[field],
                            periodic_value,
                        )
                    )
                    source = person.sources.get(field, "user")
                    if source == "default":
                        assumptions.append(
                            HouseholdAssumption(
                                field_id=f"people[{index}].{field}",
                                label=labels[field],
                                assumed_value=annual_value,
                                plain_statement=(
                                    f"The £{annual_value:,.2f} income amount is "
                                    f"treated as annual {labels[field].casefold()}."
                                ),
                                label_source="catalogue",
                            )
                        )
                person_data[field] = annual_value
                values.append(
                    HouseholdValue(
                        field_id=f"people[{index}].{field}",
                        label=labels[field],
                        value=annual_value,
                        source=source,
                        subject_entity_id=person.entity_id,
                        engine_variable=field,
                        period="annual",
                    )
                )
            people.append(person_data)

        if child_count == 0 and evidence.has_children is None:
            assumptions.append(
                HouseholdAssumption(
                    field_id="household.children",
                    label="Children",
                    assumed_value=0,
                    plain_statement="The household has no children.",
                    label_source="household_contract",
                )
            )
        is_married = evidence.is_married
        if is_married is None:
            is_married = False
            assumptions.append(
                HouseholdAssumption(
                    field_id="benunit.is_married",
                    label=labels["is_married"],
                    assumed_value=False,
                    plain_statement="The household has no partner or spouse.",
                    label_source="catalogue",
                )
            )
        values.append(
            HouseholdValue(
                field_id="benunit.is_married",
                label=labels["is_married"],
                value=is_married,
                source=evidence.sources.get("is_married", "default"),
                engine_variable="is_married",
            )
        )

        benunit: dict[str, JsonValue] = {"is_married": is_married}
        household: dict[str, JsonValue] = {}
        primary_adult_index = next(
            index
            for index, person in enumerate(evidence.people)
            if person.age is not None and person.age >= 16
        )
        childcare_expenses = evidence.childcare_expenses
        if childcare_expenses is None:
            annual_childcare_expenses = 0.0
            assumptions.append(
                HouseholdAssumption(
                    field_id=(
                        f"people[{primary_adult_index}].childcare_expenses"
                    ),
                    label=labels["childcare_expenses"],
                    assumed_value=annual_childcare_expenses,
                    plain_statement="The household has no childcare expenses.",
                    label_source="catalogue",
                )
            )
            childcare_source = "default"
        else:
            annual_childcare_expenses = childcare_expenses.annual_value()
            input_facts.extend(
                self._periodic_amount_facts(
                    labels["childcare_expenses"],
                    childcare_expenses,
                )
            )
            childcare_source = evidence.sources.get("childcare_expenses", "user")
        people[primary_adult_index]["childcare_expenses"] = annual_childcare_expenses
        values.append(
            HouseholdValue(
                field_id=f"people[{primary_adult_index}].childcare_expenses",
                label=labels["childcare_expenses"],
                value=annual_childcare_expenses,
                source=childcare_source,
                subject_entity_id=evidence.people[primary_adult_index].entity_id,
                engine_variable="childcare_expenses",
                period="annual",
            )
        )

        for field, target in (
            ("rent", household),
            ("council_tax", household),
        ):
            periodic_value = getattr(evidence, field)
            if periodic_value is None:
                if tool_input.requirements.require_housing_costs:
                    return AssembleHouseholdOutput(
                        status=HouseholdAssemblyStatus.FAILED,
                        evidence=evidence,
                        ambiguities=ambiguities,
                        error=(
                            f"Household resolution left consequential {labels[field]} "
                            "information unresolved."
                        ),
                    )
                periodic_value = PeriodicAmount(
                    amount=0,
                    frequency=AmountFrequency.ANNUAL,
                )
            annual_value = periodic_value.annual_value()
            source = evidence.sources.get(field, "default")
            if source != "default":
                input_facts.extend(
                    self._periodic_amount_facts(labels[field], periodic_value)
                )
            target[field] = annual_value
            values.append(
                HouseholdValue(
                    field_id=(
                        f"benunit.{field}"
                        if target is benunit
                        else f"household.{field}"
                    ),
                    label=labels[field],
                    value=annual_value,
                    source=source,
                    engine_variable=field,
                    period="annual",
                )
            )

        country = evidence.country or "ENGLAND"
        if evidence.country is None:
            assumptions.append(
                HouseholdAssumption(
                    field_id="household.country",
                    label="Country",
                    assumed_value=country,
                    plain_statement="The household lives in England.",
                    label_source="household_contract",
                )
            )
        household["country"] = country
        values.append(
            HouseholdValue(
                field_id="household.country",
                label="Country",
                value=country,
                source=evidence.sources.get("country", "default"),
                engine_variable="country",
            )
        )
        return AssembleHouseholdOutput(
            status=HouseholdAssemblyStatus.READY,
            evidence=evidence,
            invocation_defaults=invocation_defaults,
            ambiguities=ambiguities,
            candidate=HouseholdCandidate(
                people=tuple(people),
                benunit=benunit,
                household=household,
                field_values=tuple(values),
                entity_positions=tuple(
                    HouseholdEntityPosition(
                        entity_id=person.entity_id,
                        engine_position=f"people[{index}]",
                    )
                    for index, person in enumerate(evidence.people)
                    if person.entity_id is not None
                ),
                input_narration_facts=tuple(input_facts),
            ),
            assumptions=tuple(assumptions),
        )

    @classmethod
    def _evidence_from_values(
        cls,
        values: tuple[HouseholdValue, ...],
    ) -> HouseholdEvidence:
        people: dict[int, dict[str, object]] = {}
        household_values: dict[str, object] = {}
        household_sources: dict[str, str] = {}
        person_pattern = re.compile(r"people\[(\d+)]\.(.+)")
        for value in values:
            if value.source == "default":
                continue
            person_match = person_pattern.fullmatch(value.field_id)
            if person_match is not None:
                person_index = int(person_match.group(1))
                field = person_match.group(2)
                if field == "childcare_expenses":
                    periodic = cls._annual_amount(value.value)
                    if periodic is not None:
                        household_values["childcare_expenses"] = periodic
                        household_sources["childcare_expenses"] = "artifact"
                    continue
                if field not in {
                    "age",
                    "employment_income",
                    "self_employment_income",
                    "pension_income",
                }:
                    continue
                person = people.setdefault(person_index, {"sources": {}})
                if field == "age" and isinstance(value.value, (int, float)):
                    person[field] = int(value.value)
                elif field != "age":
                    periodic = cls._annual_amount(value.value)
                    if periodic is None:
                        continue
                    person[field] = periodic
                person["sources"][field] = "artifact"
                continue
            field = value.field_id.removeprefix("household.").removeprefix(
                "benunit."
            )
            if field in {"rent", "council_tax"}:
                periodic = cls._annual_amount(value.value)
                if periodic is not None:
                    household_values[field] = periodic
                    household_sources[field] = "artifact"
            elif field == "is_married" and isinstance(value.value, bool):
                household_values[field] = value.value
                household_sources[field] = "artifact"
            elif field == "country" and value.value in {
                "ENGLAND",
                "SCOTLAND",
                "WALES",
                "NORTHERN_IRELAND",
            }:
                household_values[field] = value.value
                household_sources[field] = "artifact"

        people_tuple = tuple(
            PersonEvidence.model_validate(people[index])
            for index in sorted(people)
        )
        if people_tuple:
            has_children = any(
                person.age is not None and person.age < 16 for person in people_tuple
            )
            household_values["has_children"] = has_children
            household_sources["has_children"] = "artifact"
        return HouseholdEvidence.model_validate(
            {
                "people": people_tuple,
                **household_values,
                "sources": household_sources,
            }
        )

    @staticmethod
    def _annual_amount(value: JsonValue) -> PeriodicAmount | None:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return PeriodicAmount(
                amount=float(value),
                frequency=AmountFrequency.ANNUAL,
            )
        return None

    @staticmethod
    def _default_only_evidence(evidence: HouseholdEvidence) -> HouseholdEvidence:
        people: list[PersonEvidence] = []
        last_default_index = -1
        for index, person in enumerate(evidence.people):
            values: dict[str, object] = {
                "entity_id": person.entity_id,
                "display_label": person.display_label,
                "relationship_to_user": person.relationship_to_user,
            }
            sources: dict[str, str] = {}
            for field in (
                "age",
                "employment_income",
                "self_employment_income",
                "pension_income",
            ):
                if person.sources.get(field) != "default":
                    continue
                values[field] = getattr(person, field)
                sources[field] = "default"
            values["sources"] = sources
            people.append(PersonEvidence.model_validate(values))
            if sources:
                last_default_index = index

        household_values: dict[str, object] = {}
        household_sources: dict[str, str] = {}
        for field in (
            "has_children",
            "is_married",
            "childcare_expenses",
            "rent",
            "council_tax",
            "country",
        ):
            if evidence.sources.get(field) != "default":
                continue
            household_values[field] = getattr(evidence, field)
            household_sources[field] = "default"
        return HouseholdEvidence.model_validate(
            {
                "people": tuple(people[: last_default_index + 1]),
                **household_values,
                "sources": household_sources,
            }
        )

    @classmethod
    def _merge_evidence(
        cls,
        retained: HouseholdEvidence,
        current: HouseholdEvidence,
    ) -> HouseholdEvidence:
        people_count = max(len(retained.people), len(current.people))
        people: list[PersonEvidence] = []
        for index in range(people_count):
            retained_person = (
                retained.people[index]
                if index < len(retained.people)
                else PersonEvidence()
            )
            current_person = (
                current.people[index]
                if index < len(current.people)
                else PersonEvidence()
            )
            updates: dict[str, object] = {}
            sources = dict(retained_person.sources)
            for identity_field in (
                "entity_id",
                "display_label",
                "relationship_to_user",
            ):
                current_identity = getattr(current_person, identity_field)
                if current_identity is not None:
                    updates[identity_field] = current_identity
            for field in (
                "age",
                "employment_income",
                "self_employment_income",
                "pension_income",
            ):
                current_value = getattr(current_person, field)
                if current_value is not None:
                    updates[field] = current_value
                    sources[field] = current_person.sources.get(field, "user")
            updates["sources"] = sources
            people.append(retained_person.model_copy(update=updates))

        household_updates: dict[str, object] = {"people": tuple(people)}
        household_sources = dict(retained.sources)
        for field in (
            "has_children",
            "is_married",
            "childcare_expenses",
            "rent",
            "council_tax",
            "country",
        ):
            current_value = getattr(current, field)
            if current_value is not None:
                household_updates[field] = current_value
                household_sources[field] = current.sources.get(field, "user")
        household_updates["sources"] = household_sources
        return retained.model_copy(update=household_updates)

    @staticmethod
    def _as_current_user_evidence(
        evidence: HouseholdEvidence,
        ambiguities: tuple[HouseholdEvidenceAmbiguity, ...],
    ) -> HouseholdEvidence:
        people = []
        for index, person in enumerate(evidence.people):
            person_updates: dict[str, object] = {}
            for ambiguity in ambiguities:
                if ambiguity.kind not in {
                    HouseholdEvidenceAmbiguityKind.INCOME_OWNER,
                    HouseholdEvidenceAmbiguityKind.INCOME_FREQUENCY,
                }:
                    continue
                if ambiguity.field not in {
                    "employment_income",
                    "self_employment_income",
                    "pension_income",
                }:
                    continue
                if ambiguity.person_indices and index not in ambiguity.person_indices:
                    continue
                person_updates[ambiguity.field] = None
            normalized = person.model_copy(update=person_updates)
            people.append(
                normalized.model_copy(
                    update={
                        "sources": {
                            field: "user"
                            for field in (
                                "age",
                                "employment_income",
                                "self_employment_income",
                                "pension_income",
                            )
                            if getattr(normalized, field) is not None
                        }
                    }
                )
            )
        sources = {
            field: "user"
            for field in (
                "has_children",
                "is_married",
                "childcare_expenses",
                "rent",
                "council_tax",
                "country",
            )
            if getattr(evidence, field) is not None
        }
        return evidence.model_copy(
            update={
                "people": tuple(people),
                "sources": sources,
            }
        )

    @classmethod
    def _merge_ambiguities(
        cls,
        retained: tuple[HouseholdEvidenceAmbiguity, ...],
        current: tuple[HouseholdEvidenceAmbiguity, ...],
        current_evidence: HouseholdEvidence,
    ) -> tuple[HouseholdEvidenceAmbiguity, ...]:
        current_keys = {cls._ambiguity_key(item) for item in current}
        merged = [
            item
            for item in retained
            if cls._ambiguity_key(item) not in current_keys
            and not cls._ambiguity_resolved(item, current_evidence)
        ]
        merged.extend(current)
        return tuple(
            {
                cls._ambiguity_key(item): item
                for item in merged
            }.values()
        )

    @staticmethod
    def _ambiguity_key(ambiguity: HouseholdEvidenceAmbiguity):
        return ambiguity.kind, ambiguity.field, ambiguity.person_indices

    @staticmethod
    def _ambiguity_resolved(
        ambiguity: HouseholdEvidenceAmbiguity,
        current_evidence: HouseholdEvidence,
    ) -> bool:
        if ambiguity.kind is HouseholdEvidenceAmbiguityKind.ADULT_RELATIONSHIP:
            return current_evidence.is_married is not None
        if ambiguity.kind not in {
            HouseholdEvidenceAmbiguityKind.INCOME_OWNER,
            HouseholdEvidenceAmbiguityKind.INCOME_FREQUENCY,
        }:
            return False
        if ambiguity.field not in {
            "employment_income",
            "self_employment_income",
            "pension_income",
        }:
            return False
        candidate_indices = (
            ambiguity.person_indices
            if ambiguity.person_indices
            else tuple(range(len(current_evidence.people)))
        )
        return any(
            index < len(current_evidence.people)
            and getattr(current_evidence.people[index], ambiguity.field) is not None
            for index in candidate_indices
        )

    @staticmethod
    def _periodic_amount_facts(
        label: str,
        value: PeriodicAmount,
    ) -> tuple[NumericalFact, ...]:
        unit = {
            AmountFrequency.WEEKLY: "GBP/week",
            AmountFrequency.MONTHLY: "GBP/month",
            AmountFrequency.ANNUAL: "GBP/year",
        }[value.frequency]
        facts = [NumericalFact(label=label, value=value.amount, unit=unit)]
        annual_value = value.annual_value()
        if value.frequency is not AmountFrequency.ANNUAL:
            facts.append(
                NumericalFact(
                    label=f"Annual {label.casefold()}",
                    value=annual_value,
                    unit="GBP/year",
                )
            )
        return tuple(facts)

    async def _catalogue_labels(self, context):
        labels = {}
        for field, fallback in _CATALOGUE_FIELDS.items():
            result = await context.invoke_tool("get_variable", {"name": field})
            if not isinstance(result, SafeToolOutput):
                return None
            variable = result.root.get("variable")
            if not isinstance(variable, dict):
                return None
            label = variable.get("label")
            if not isinstance(label, str) or not label.strip():
                return None
            labels[field] = label
        return labels

    @staticmethod
    def _person_subject(person: PersonEvidence, index: int) -> str:
        if person.display_label:
            return person.display_label[:1].upper() + person.display_label[1:]
        if person.relationship_to_user == "self":
            return "You"
        del index
        return "Another household member"


class HouseholdAnalysisInput(StrictModel):
    description: str
    year: int | None = None
    referenced_household_id: str | None = Field(
        default=None,
        description=(
            "Compatible retained HouseholdRef artifact identifier. Supply it when "
            "the user clearly corrects, extends, or recalculates the same household; "
            "omit it for a clearly separate household."
        ),
    )
    referenced_policy_scenario_id: str | None = None
    reform_instruction: str | None = None
    requested_outputs: tuple[str, ...] = Field(
        default=(),
        description=(
            "Every household metric the user explicitly asks to calculate, using "
            "their ordinary wording; for example, income tax, National Insurance, "
            "Universal Credit, or net income."
        ),
    )
    start_new_invocation: bool = Field(
        default=False,
        description=(
            "True only when the user clearly starts a separate household calculation "
            "instead of answering the single pending household clarification."
        ),
    )


class HouseholdAnalysisDraft(HouseholdAnalysisInput):
    invocation_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("invocation_id", "resuming_invocation_id"),
    )
    context_scope_id: str | None = None
    context_revision: int | None = None
    fact_requirements: tuple[FactRequirement, ...] = ()
    evidence: HouseholdEvidence = Field(default_factory=HouseholdEvidence)
    invocation_defaults: HouseholdInvocationDefaults = Field(
        default_factory=HouseholdInvocationDefaults
    )
    ambiguities: tuple[HouseholdEvidenceAmbiguity, ...] = ()
    pending_fields: tuple[str, ...] = ()
    authoritative_messages: tuple[str, ...] = ()
    unresolved_sterling_mentions: tuple[str, ...] = ()


class HouseholdOutputIssue(StrictModel):
    request: str
    guidance: str


class HouseholdAnalysisOutput(StrictModel):
    result: HouseholdResultRef
    assumptions: tuple[HouseholdAssumption, ...]
    year_source: InputSource
    output_issues: tuple[HouseholdOutputIssue, ...] = ()
    narration_facts: tuple[NumericalFact, ...] = ()
    assumption_statements: tuple[str, ...] = ()
    narration_requirement: str = (
        "Report every calculated value in result.outputs and explain every item in "
        "output_issues before offering optional follow-up analysis. Do not claim an "
        "amount, entitlement, or ineligibility for a benefit absent from "
        "result.outputs."
    )
    narration_fallback: str


class HouseholdAnalysisCapability(
    Capability[HouseholdAnalysisInput, HouseholdAnalysisOutput]
):
    spec = CapabilitySpec(
        identifier="household_analysis",
        version="1",
        description=(
            "Assemble a described household, state every material default in plain "
            "language, validate it, and calculate deterministic household impacts."
        ),
        required_use=(
            "Must be invoked immediately for tax amounts, benefit amounts, benefit "
            "entitlements, or policy impacts for a described or retained household, "
            "even when household details are incomplete. The conversational model "
            "must not ask its own household-input questions before this capability runs."
        ),
        visibility=Visibility.PUBLIC,
        allowed_callers=frozenset({CallerType.MODEL, CallerType.CAPABILITY}),
        input_model=HouseholdAnalysisInput,
        output_model=HouseholdAnalysisOutput,
        accepted_artifacts=(
            ArtifactContract(artifact_type="household", schema_version="1"),
            ArtifactContract(artifact_type="policy_scenario", schema_version="1"),
        ),
        produced_artifacts=(
            ArtifactContract(artifact_type="household", schema_version="1"),
            ArtifactContract(artifact_type="household_result", schema_version="1"),
        ),
        dependencies=(
            CapabilityDependency(
                capability_id="policy_reform",
                artifact=ArtifactContract(
                    artifact_type="policy_scenario",
                    schema_version="1",
                ),
            ),
        ),
        tool_dependencies=(
            "assemble_household_candidate",
            "validate_household",
            "run_household_simulation",
            "search_variables",
            "get_variable",
            "extract_result_findings",
        ),
    )

    def __init__(
        self,
        engine_fact_projector: HouseholdEngineFactProjector | None = None,
    ) -> None:
        self._engine_fact_projector = engine_fact_projector
        self._input_completeness = HouseholdInputCompleteness()

    async def run(self, capability_input: HouseholdAnalysisInput, context):
        waiting, selection_outcome = await self._select_waiting(
            capability_input,
            context,
        )
        if selection_outcome is not None:
            return selection_outcome
        retained_draft = (
            HouseholdAnalysisDraft.model_validate(waiting.partial_input.model_dump())
            if waiting is not None
            else None
        )
        effective_input = self._merge_capability_input(
            retained_draft,
            capability_input,
        )
        context_view = (
            HouseholdContextView(context.conversation_context)
            if context.conversation_context is not None
            else None
        )
        pending_resolutions = (
            context_view.pending_fact_resolutions()
            if context_view is not None
            else ()
        )
        if pending_resolutions:
            return NeedsInput(
                prompt=self._clarification_prompt(
                    tuple(item.prompt for item in pending_resolutions)
                ),
                missing_fields=tuple(
                    f"fact_resolution:{item.proposal_id}"
                    for item in pending_resolutions
                ),
                partial_input=(
                    self._public_partial_input(retained_draft)
                    if retained_draft is not None
                    else effective_input.model_dump(mode="json", exclude_none=True)
                ),
            )
        existing_household = await self._find_household(effective_input, context)
        scenario = await self._find_scenario(effective_input, context)
        defaulted_to_current_policy = (
            scenario is None and effective_input.reform_instruction is None
        )
        resolved_year = resolve_policy_year(
            explicit_year=(
                effective_input.year
                if effective_input.year is not None
                else context_view.policy_year() if context_view is not None else None
            ),
            referenced_year=(
                scenario.year
                if scenario is not None
                else existing_household.year if existing_household is not None else None
            ),
        )
        requested_requests = self._effective_requested_output_requests(
            effective_input.requested_outputs,
            context_view=context_view,
            current_user_message=context.current_user_message,
        )
        requested, issues = await self._requested_outputs(
            requested_requests,
            context,
        )
        requirements = self._calculation_requirements(requested)
        context_evidence = context_view.evidence() if context_view is not None else None
        if context_view is not None:
            requirements = requirements.model_copy(
                update={
                    "context_scope_id": context_view.scope_id,
                    "household_entity_id": context_view.household_entity_id,
                }
            )
        assembly = await context.invoke_tool(
            "assemble_household_candidate",
            {
                "description": (
                    context.current_user_message or capability_input.description
                ),
                "requirements": requirements.model_dump(mode="json"),
                "existing_values": (
                    [value.model_dump(mode="json") for value in existing_household.values]
                    if existing_household is not None
                    else []
                ),
                "retained_evidence": (
                    context_evidence.model_dump(mode="json")
                    if context_evidence is not None
                    else retained_draft.evidence.model_dump(mode="json")
                    if retained_draft is not None
                    else {}
                ),
                "invocation_defaults": (
                    retained_draft.invocation_defaults.model_dump(mode="json")
                    if retained_draft is not None
                    else {}
                ),
                "retained_ambiguities": (
                    [
                        item.model_dump(mode="json")
                        for item in retained_draft.ambiguities
                    ]
                    if retained_draft is not None
                    else []
                ),
                "pending_fields": (
                    list(retained_draft.pending_fields)
                    if retained_draft is not None
                    else []
                ),
            },
        )
        if not isinstance(assembly, AssembleHouseholdOutput):
            raise TypeError("Household assembly returned an incompatible output.")
        if assembly.status is HouseholdAssemblyStatus.NEEDS_INPUT:
            draft = HouseholdAnalysisDraft.model_validate(
                {
                    **effective_input.model_dump(mode="json"),
                    "requested_outputs": requested,
                    "evidence": assembly.evidence.model_dump(mode="json"),
                    "invocation_defaults": assembly.invocation_defaults.model_dump(
                        mode="json"
                    ),
                    "authoritative_messages": self._authoritative_messages(
                        retained_draft,
                        context.current_user_message,
                    ),
                    "ambiguities": [
                        item.model_dump(mode="json")
                        for item in assembly.ambiguities
                    ],
                    "pending_fields": list(assembly.missing_fields),
                    "fact_requirements": [
                        item.model_dump(mode="json")
                        for item in assembly.fact_requirements
                    ],
                }
            )
            draft = await self._persist_draft(draft, waiting, context)
            return NeedsInput(
                prompt=self._clarification_prompt(assembly.questions),
                missing_fields=assembly.missing_fields,
                partial_input=self._public_partial_input(draft),
                fact_requirements=assembly.fact_requirements,
                capability_invocation=self._capability_invocation(draft),
            )
        if assembly.status is HouseholdAssemblyStatus.FAILED or assembly.candidate is None:
            return Failed(
                safe_message=assembly.error or "Household assembly failed.",
                error_code="household_assembly_contract",
            )

        draft = HouseholdAnalysisDraft.model_validate(
            {
                **effective_input.model_dump(mode="json"),
                "requested_outputs": requested,
                "evidence": assembly.evidence.model_dump(mode="json"),
                "invocation_defaults": assembly.invocation_defaults.model_dump(
                    mode="json"
                ),
                "authoritative_messages": self._authoritative_messages(
                    retained_draft,
                    context.current_user_message,
                ),
                "ambiguities": [
                    item.model_dump(mode="json") for item in assembly.ambiguities
                ],
                "pending_fields": [],
                "fact_requirements": [],
            }
        )

        unresolved_mentions = self._input_completeness.unresolved_mentions(
            authoritative_messages=draft.authoritative_messages,
            verified_amounts=tuple(
                float(fact.value)
                for fact in assembly.candidate.input_narration_facts
                if fact.unit.startswith("GBP/")
            )
            + assembly.invocation_defaults.sterling_amounts(),
            excluded_texts=(
                (effective_input.reform_instruction or ""),
            ),
        )
        if unresolved_mentions:
            mention_text = self._natural_list(
                tuple(item.text for item in unresolved_mentions)
            )
            draft = draft.model_copy(
                update={
                    "pending_fields": tuple(
                        f"unresolved_sterling:{item.amount}"
                        for item in unresolved_mentions
                    ),
                    "unresolved_sterling_mentions": tuple(
                        item.text for item in unresolved_mentions
                    ),
                }
            )
            draft = await self._persist_draft(draft, waiting, context)
            return NeedsInput(
                prompt=(
                    f"I could not connect {mention_text} to a validated household "
                    "input. Please restate what each amount represents, who receives "
                    "or pays it, and whether it is weekly, monthly, or annual."
                ),
                missing_fields=tuple(draft.pending_fields),
                partial_input=self._public_partial_input(draft),
            )

        if effective_input.reform_instruction:
            scenario_outcome = await context.invoke_capability(
                "policy_reform",
                {
                    "instruction": effective_input.reform_instruction,
                    "year": resolved_year.year,
                    "referenced_policy_scenario_id": (
                        scenario.artifact_id if scenario is not None else None
                    ),
                },
            )
            if not isinstance(scenario_outcome, Completed) or not isinstance(
                scenario_outcome.value,
                PolicyReformOutput,
            ):
                return await self._forward_reform_outcome(
                    scenario_outcome,
                    draft,
                    waiting,
                    context,
                )
            scenario = scenario_outcome.value.scenario
        elif scenario is None:
            scenario_outcome = await context.invoke_capability(
                "policy_reform",
                {"instruction": "current law", "year": resolved_year.year},
            )
            if not isinstance(scenario_outcome, Completed) or not isinstance(
                scenario_outcome.value,
                PolicyReformOutput,
            ):
                return await self._forward_reform_outcome(
                    scenario_outcome,
                    draft,
                    waiting,
                    context,
                )
            scenario = scenario_outcome.value.scenario

        candidate = assembly.candidate
        if (
            self._engine_fact_projector is not None
            and context_view is not None
            and context.conversation_context is not None
        ):
            candidate = self._merge_engine_facts(
                candidate,
                self._engine_fact_projector.project(
                    context.conversation_context,
                    scope_id=context_view.scope_id,
                    person_entity_ids=context_view.person_entity_ids,
                    household_entity_id=context_view.household_entity_id,
                ),
            )
        reform = {
            change.parameter_path: change.value for change in scenario.verified_changes
        }
        simulation_input = {
            "people": list(candidate.people),
            "benunit": candidate.benunit,
            "household": candidate.household,
            "year": resolved_year.year,
            "reform": reform or None,
            "extra_variables": list(requested),
        }
        validation = await context.invoke_tool("validate_household", simulation_input)
        if not isinstance(validation, SafeToolOutput):
            raise TypeError("Household validation returned an incompatible output.")
        if validation.root.get("valid") is not True:
            missing_fields = self._validation_fields(validation.root)
            draft = draft.model_copy(update={"pending_fields": missing_fields})
            draft = await self._persist_draft(draft, waiting, context)
            return NeedsInput(
                prompt=self._clarification_prompt(
                    (self._validation_prompt(validation.root),),
                ),
                missing_fields=missing_fields,
                partial_input=self._public_partial_input(draft),
            )
        if waiting is not None:
            await context.remove_waiting(waiting.invocation_id)
        household_ref = HouseholdRef(
            provenance=self._provenance(context, "validated household"),
            year=resolved_year.year,
            household_revision=self._household_revision(candidate),
            catalogue_version=scenario.catalogue_version,
            calculation_engine_version=scenario.calculation_engine_version,
            values=candidate.field_values,
            context_scope_id=(
                context.conversation_context.focus.scope_id
                if context.conversation_context is not None
                else None
            ),
            context_revision=(
                context.conversation_context.revision
                if context.conversation_context is not None
                else None
            ),
            entity_positions=candidate.entity_positions,
        )
        household_ref = await context.save_artifact(household_ref)
        simulation = await context.invoke_tool("run_household_simulation", simulation_input)
        if not isinstance(simulation, SafeToolOutput):
            raise TypeError("Household simulation returned an incompatible output.")
        if "error" in simulation.root:
            return Failed(
                safe_message="The deterministic household calculation failed.",
                error_code="household_simulation_failed",
            )
        outputs = self._extract_outputs(simulation.root, requested)
        result = HouseholdResultRef(
            provenance=self._provenance(context, "household simulation"),
            year=resolved_year.year,
            household_artifact_id=household_ref.artifact_id,
            policy_scenario_artifact_id=scenario.artifact_id,
            scenario_revision=scenario.scenario_revision,
            calculation_engine_version=scenario.calculation_engine_version,
            outputs=outputs,
            context_scope_id=household_ref.context_scope_id,
            context_revision=household_ref.context_revision,
        )
        result = await context.save_artifact(result)
        extracted = await context.invoke_tool(
            "extract_result_findings",
            {"outputs": [output.model_dump(mode="json") for output in outputs]},
        )
        if not isinstance(extracted, ExtractResultFindingsOutput):
            raise TypeError("Household finding extraction returned an incompatible output.")
        output_facts = tuple(
            fact
            for finding in extracted.findings
            if finding.value is not None
            for fact in self._output_narration_facts(finding)
        )
        facts = (
            *candidate.input_narration_facts,
            NumericalFact(
                label="Policy year",
                value=resolved_year.year,
                unit="year",
            ),
            *output_facts,
        )
        completed_assumptions = self._completed_assumptions(
            assembly.assumptions,
            year=resolved_year.year,
            year_source=resolved_year.source,
            defaulted_to_current_policy=defaulted_to_current_policy,
        )
        return Completed(
            value=HouseholdAnalysisOutput(
                result=result,
                assumptions=completed_assumptions,
                year_source=resolved_year.source,
                output_issues=issues,
                narration_facts=facts,
                assumption_statements=tuple(
                    assumption.plain_statement
                    for assumption in completed_assumptions
                ),
                narration_fallback=self._narration_fallback(
                    outputs,
                    completed_assumptions,
                    issues,
                ),
            )
        )

    @staticmethod
    def _merge_engine_facts(
        candidate: HouseholdCandidate,
        projection: HouseholdEngineInputs,
    ) -> HouseholdCandidate:
        """Add catalogue-backed facts while retaining assembled explicit values."""

        people = [dict(item) for item in candidate.people]
        positions = {
            item.entity_id: int(
                item.engine_position.removeprefix("people[").removesuffix("]")
            )
            for item in candidate.entity_positions
            if item.engine_position.startswith("people[")
            and item.engine_position.endswith("]")
            and item.engine_position.removeprefix("people[")
            .removesuffix("]")
            .isdigit()
        }
        for person in projection.people:
            index = positions.get(person.entity_id)
            if index is None or index >= len(people):
                continue
            for variable_name, value in person.values.items():
                people[index].setdefault(variable_name, value)
        return candidate.model_copy(
            update={
                "people": tuple(people),
                "benunit": {**projection.benunit, **candidate.benunit},
                "household": {**projection.household, **candidate.household},
            }
        )

    @staticmethod
    def _output_narration_facts(finding) -> tuple[NumericalFact, ...]:
        facts = [
            NumericalFact(
                label=finding.label,
                value=finding.value,
                unit=finding.unit,
            )
        ]
        if finding.unit == "GBP/year":
            facts.extend(
                (
                    NumericalFact(
                        label=f"Monthly {finding.label.casefold()}",
                        value=finding.value / 12,
                        unit="GBP/month",
                    ),
                    NumericalFact(
                        label=f"Weekly {finding.label.casefold()}",
                        value=finding.value / 52,
                        unit="GBP/week",
                    ),
                )
            )
        return tuple(facts)

    @staticmethod
    def _narration_fallback(outputs, assumptions, issues) -> str:
        result_lines = []
        for output in outputs:
            if output.value is None:
                result_lines.append(f"- {output.label}: unavailable")
            elif output.unit == "GBP/year":
                result_lines.append(
                    f"- {output.label}: £{output.value:,.2f} per year"
                )
            else:
                result_lines.append(
                    f"- {output.label}: {output.value:g} {output.unit}"
                )
        if result_lines:
            paragraphs = ["### Results\n\n" + "\n".join(result_lines)]
        else:
            paragraphs = ["The calculation did not return a supported household output."]
        if assumptions:
            statements = "\n".join(
                f"- {item.plain_statement}" for item in assumptions
            )
            paragraphs.append(f"### Assumptions used\n\n{statements}")
        if issues:
            issue_text = " ".join(
                f"I could not calculate {item.request}: {item.guidance}"
                for item in issues
            )
            paragraphs.append(issue_text)
        return "\n\n".join(paragraphs)

    @staticmethod
    def _completed_assumptions(
        household_assumptions,
        *,
        year,
        year_source,
        defaulted_to_current_policy,
    ):
        assumptions = list(household_assumptions)
        if year_source is InputSource.SERVER_DEFAULT:
            assumptions.insert(
                0,
                HouseholdAssumption(
                    field_id="policy.year",
                    label="Policy year",
                    assumed_value=year,
                    plain_statement=f"Policy year: {year}.",
                    label_source="system_default",
                ),
            )
        if defaulted_to_current_policy:
            assumptions.insert(
                1 if year_source is InputSource.SERVER_DEFAULT else 0,
                HouseholdAssumption(
                    field_id="policy.scenario",
                    label="Policy scenario",
                    assumed_value="current_policy",
                    plain_statement="Policy scenario: current policy.",
                    label_source="system_default",
                ),
            )
        return tuple(assumptions)

    async def _forward_reform_outcome(
        self,
        outcome,
        draft,
        waiting,
        context,
    ):
        if not isinstance(outcome, NeedsInput):
            return outcome
        draft = draft.model_copy(update={"pending_fields": ("reform_instruction",)})
        draft = await self._persist_draft(draft, waiting, context)
        return NeedsInput(
            prompt=outcome.prompt,
            missing_fields=("reform_instruction",),
            partial_input=self._public_partial_input(
                draft,
                exclude_defaults=True,
            ),
        )

    @classmethod
    async def _select_waiting(cls, capability_input, context):
        waiting = await context.waiting_invocations(cls.spec.identifier)
        if capability_input.start_new_invocation:
            return None, None
        active_scope_id = (
            context.conversation_context.focus.scope_id
            if context.conversation_context is not None
            else None
        )
        compatible = tuple(
            item
            for item in waiting
            if cls._waiting_scope_id(item) in {None, active_scope_id}
        )
        pending_questions = (
            tuple(
                question
                for question in context.conversation_context.pending_questions
                if question.capability_id == cls.spec.identifier
                and question.capability_invocation is not None
                and question.capability_invocation.context_scope_id
                in {active_scope_id, None}
            )
            if context.conversation_context is not None
            else ()
        )
        compatible_by_id = {item.invocation_id: item for item in compatible}
        linked = tuple(
            compatible_by_id[question.capability_invocation.invocation_id]
            for question in pending_questions
            if question.capability_invocation is not None
            and question.capability_invocation.invocation_id in compatible_by_id
        )
        if len(linked) == 1:
            return linked[0], None
        if len(linked) > 1 and context.conversation_context is not None:
            answered = tuple(
                compatible_by_id[question.capability_invocation.invocation_id]
                for question in pending_questions
                if question.capability_invocation is not None
                and question.capability_invocation.invocation_id in compatible_by_id
                and (
                    question.status is PendingQuestionStatus.ANSWER_RECEIVED
                    or cls._requirements_satisfied(
                        context.conversation_context,
                        question.requirements,
                    )
                )
            )
            if len(answered) == 1:
                return answered[0], None
        if not linked and len(compatible) == 1:
            # Repair compatibility for version-one contexts that lost or never
            # stored the pending-question link.
            return compatible[0], None
        candidates = linked or compatible
        if len(candidates) > 1:
            choices = "; ".join(
                getattr(item.partial_input, "description", "household calculation")
                for item in candidates
            )
            return None, NeedsInput(
                prompt=(
                    "More than one household calculation is waiting for information. "
                    f"Which one do you want to continue? {choices}"
                ),
                missing_fields=("pending_household_selection",),
                partial_input={},
            )
        return None, None

    @staticmethod
    def _waiting_scope_id(waiting) -> str | None:
        return getattr(waiting.partial_input, "context_scope_id", None)

    @staticmethod
    def _requirements_satisfied(context, requirements) -> bool:
        if not requirements:
            return False
        for requirement in requirements:
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

    @staticmethod
    def _merge_capability_input(retained_draft, current):
        if retained_draft is None:
            return current
        return HouseholdAnalysisInput(
            description=current.description,
            year=current.year if current.year is not None else retained_draft.year,
            referenced_household_id=(
                current.referenced_household_id
                if current.referenced_household_id is not None
                else retained_draft.referenced_household_id
            ),
            referenced_policy_scenario_id=(
                current.referenced_policy_scenario_id
                if current.referenced_policy_scenario_id is not None
                else retained_draft.referenced_policy_scenario_id
            ),
            reform_instruction=(
                current.reform_instruction
                if current.reform_instruction is not None
                else retained_draft.reform_instruction
            ),
            requested_outputs=(
                retained_draft.requested_outputs
            ),
            start_new_invocation=False,
        )

    @staticmethod
    async def _persist_draft(draft, waiting, context):
        invocation_id = (
            waiting.invocation_id
            if waiting is not None
            else context.capability_invocation_id
        )
        stored_draft = draft.model_copy(
            update={
                "invocation_id": invocation_id,
                "context_scope_id": (
                    context.conversation_context.focus.scope_id
                    if context.conversation_context is not None
                    else draft.context_scope_id
                ),
                "context_revision": (
                    context.conversation_context.revision
                    if context.conversation_context is not None
                    else draft.context_revision
                ),
                "start_new_invocation": False,
            }
        )
        if context.conversation_context is not None:
            stored_draft = stored_draft.model_copy(
                update={
                    "evidence": HouseholdEvidence(),
                    "ambiguities": (),
                }
            )
        if waiting is None:
            await context.persist_waiting(stored_draft)
        else:
            await context.update_waiting(invocation_id, stored_draft)
        return stored_draft

    @staticmethod
    def _capability_invocation(
        draft: HouseholdAnalysisDraft,
    ) -> CapabilityInvocationReference | None:
        if (
            draft.invocation_id is None
            or draft.context_scope_id is None
            or draft.context_revision is None
        ):
            return None
        return CapabilityInvocationReference(
            invocation_id=draft.invocation_id,
            capability_id=HouseholdAnalysisCapability.spec.identifier,
            capability_version=HouseholdAnalysisCapability.spec.version,
            context_scope_id=draft.context_scope_id,
            context_revision=draft.context_revision,
        )

    @staticmethod
    def _public_partial_input(draft, *, exclude_defaults=False):
        return draft.model_dump(
            mode="json",
            exclude={
                "invocation_id",
                "context_scope_id",
                "context_revision",
                "fact_requirements",
                "evidence",
                "invocation_defaults",
                "ambiguities",
                "pending_fields",
                "authoritative_messages",
                "unresolved_sterling_mentions",
            },
            exclude_none=True,
            exclude_defaults=exclude_defaults,
        )

    @staticmethod
    def _authoritative_messages(retained_draft, current_message):
        retained = (
            retained_draft.authoritative_messages
            if retained_draft is not None
            else ()
        )
        if not current_message or current_message in retained:
            return retained
        return (*retained, current_message)

    @staticmethod
    def _natural_list(items):
        if len(items) == 1:
            return items[0]
        if len(items) == 2:
            return f"{items[0]} and {items[1]}"
        return f"{', '.join(items[:-1])}, and {items[-1]}"

    @staticmethod
    def _clarification_prompt(questions):
        return " ".join(dict.fromkeys(questions))

    @staticmethod
    async def _find_household(capability_input, context):
        artifacts = await context.find_artifacts(HouseholdRef)
        if capability_input.referenced_household_id is not None:
            return next(
                (
                    item
                    for item in artifacts
                    if item.artifact_id == capability_input.referenced_household_id
                ),
                None,
            )
        if context.conversation_context is None:
            return None
        scope_id = context.conversation_context.focus.scope_id
        compatible = tuple(
            item for item in artifacts if item.context_scope_id == scope_id
        )
        if not compatible:
            return None
        return max(
            compatible,
            key=lambda item: (item.context_revision or -1, item.created_at),
        )

    @staticmethod
    async def _find_scenario(capability_input, context):
        if capability_input.referenced_policy_scenario_id is None:
            return None
        artifacts = await context.find_artifacts(PolicyScenarioRef)
        return next(
            (
                item
                for item in artifacts
                if item.artifact_id == capability_input.referenced_policy_scenario_id
            ),
            None,
        )

    @staticmethod
    async def _requested_outputs(requests, context):
        selected = []
        issues = []
        for request in requests:
            normalized = " ".join(request.casefold().replace("_", " ").split())
            grouped_identifiers = _HOUSEHOLD_OUTPUT_GROUPS.get(normalized)
            if grouped_identifiers is not None:
                missing_group_member = False
                for identifier in grouped_identifiers:
                    result = await context.invoke_tool(
                        "get_variable",
                        {"name": identifier},
                    )
                    variable = (
                        result.root.get("variable")
                        if isinstance(result, SafeToolOutput)
                        else None
                    )
                    if not (
                        isinstance(variable, dict)
                        and variable.get("name") == identifier
                    ):
                        missing_group_member = True
                        issues.append(
                            HouseholdOutputIssue(
                                request=request,
                                guidance=(
                                    "A required tax output is unavailable in the "
                                    "authoritative variable catalogue."
                                ),
                            )
                        )
                        break
                if missing_group_member:
                    continue
                selected.extend(
                    identifier
                    for identifier in grouped_identifiers
                    if identifier not in selected
                )
                continue
            aliased_identifier = _HOUSEHOLD_OUTPUT_ALIASES.get(normalized)
            if aliased_identifier is not None:
                result = await context.invoke_tool(
                    "get_variable",
                    {"name": aliased_identifier},
                )
                variable = (
                    result.root.get("variable")
                    if isinstance(result, SafeToolOutput)
                    else None
                )
                if (
                    isinstance(variable, dict)
                    and variable.get("name") == aliased_identifier
                ):
                    if aliased_identifier not in selected:
                        selected.append(aliased_identifier)
                    continue
                issues.append(
                    HouseholdOutputIssue(
                        request=request,
                        guidance=(
                            "The mapped household output is unavailable in the "
                            "authoritative variable catalogue."
                        ),
                    )
                )
                continue
            result = await context.invoke_tool(
                "search_variables",
                {"query": request, "limit": 10},
            )
            rows = (
                result.root.get("variables")
                if isinstance(result, SafeToolOutput)
                else None
            )
            matches = [
                row
                for row in rows or []
                if isinstance(row, dict) and isinstance(row.get("name"), str)
            ]
            if not matches:
                issues.append(
                    HouseholdOutputIssue(
                        request=request,
                        guidance="No authoritative household output matched this request.",
                    )
                )
                continue
            exact = [
                row
                for row in matches
                if normalized
                in {
                    str(row["name"]).casefold().replace("_", " "),
                    str(row.get("label", "")).casefold(),
                }
            ]
            chosen = exact[0] if len(exact) == 1 else None
            if chosen is None:
                issues.append(
                    HouseholdOutputIssue(
                        request=request,
                        guidance=(
                            "No unambiguous authoritative household output exactly "
                            "matched this request; please clarify."
                        ),
                    )
                )
                continue
            if chosen["name"] not in selected:
                selected.append(chosen["name"])
        if not selected:
            selected.append("household_net_income")
        return tuple(selected), tuple(issues)

    @staticmethod
    def _effective_requested_output_requests(
        explicit_requests: tuple[str, ...],
        *,
        context_view: HouseholdContextView | None,
        current_user_message: str | None,
    ) -> tuple[str, ...]:
        combined = list(dict.fromkeys(explicit_requests))
        detected = HouseholdAnalysisCapability._requested_outputs_from_message(
            current_user_message
        )
        combined.extend(item for item in detected if item not in combined)
        if combined:
            return tuple(combined)
        if context_view is not None:
            retained = context_view.requested_outputs()
            if retained:
                return retained
        return ()

    @staticmethod
    def _requested_outputs_from_message(
        current_user_message: str | None,
    ) -> tuple[str, ...]:
        if not current_user_message:
            return ()
        normalized = " ".join(
            current_user_message.casefold().replace("_", " ").split()
        )
        matched: list[str] = []
        specific_tax_requested = False
        for label, output_id in sorted(
            _HOUSEHOLD_OUTPUT_ALIASES.items(),
            key=lambda item: len(item[0]),
            reverse=True,
        ):
            if re.search(rf"\b{re.escape(label)}\b", normalized) is None:
                continue
            if output_id not in matched:
                matched.append(output_id)
            if output_id in _TAX_ONLY_HOUSEHOLD_OUTPUTS:
                specific_tax_requested = True

        if (
            not specific_tax_requested
            and re.search(r"\btaxes?\b", normalized) is not None
        ):
            matched.extend(
                output_id
                for output_id in (
                    "income_tax",
                    "national_insurance",
                    "household_tax",
                )
                if output_id not in matched
            )
        return tuple(matched)

    @staticmethod
    def _calculation_requirements(
        requested_output_ids: tuple[str, ...],
    ) -> HouseholdCalculationRequirements:
        tax_only = bool(requested_output_ids) and set(requested_output_ids).issubset(
            _TAX_ONLY_HOUSEHOLD_OUTPUTS
        )
        return HouseholdCalculationRequirements(
            requested_output_ids=requested_output_ids,
            require_housing_costs=not tax_only,
        )

    @classmethod
    def _extract_outputs(cls, payload, requested):
        reform_applied = payload.get("reform_applied") is True
        outputs = []
        for variable in requested:
            if reform_applied:
                baseline = cls._find_number(payload.get("baseline"), variable)
                reform = cls._find_number(payload.get("reform"), variable)
                for metric, value in (
                    ("baseline", baseline),
                    ("reform", reform),
                    (
                        "change",
                        reform - baseline
                        if reform is not None and baseline is not None
                        else None,
                    ),
                ):
                    outputs.append(
                        AggregateValue(
                            output_id=variable,
                            metric_id=metric,
                            label=variable.replace("_", " ").title(),
                            value=value,
                            unit="GBP/year",
                            dimensions=(
                                AggregateDimension(name="scenario", value=metric),
                            ),
                        )
                    )
            else:
                outputs.append(
                    AggregateValue(
                        output_id=variable,
                        metric_id="current_law",
                        label=variable.replace("_", " ").title(),
                        value=cls._find_number(payload, variable),
                        unit="GBP/year",
                    )
                )
        return tuple(outputs)

    @classmethod
    def _find_number(cls, value, key):
        if isinstance(value, dict):
            if key in value and isinstance(value[key], (int, float)):
                return value[key]
            for nested in value.values():
                found = cls._find_number(nested, key)
                if found is not None:
                    return found
        if isinstance(value, list):
            for nested in value:
                found = cls._find_number(nested, key)
                if found is not None:
                    return found
        return None

    @staticmethod
    def _validation_prompt(payload):
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            messages = tuple(
                error["message"]
                for error in errors
                if isinstance(error, dict)
                and isinstance(error.get("message"), str)
                and error["message"].strip()
            )
            if messages:
                return " ".join(dict.fromkeys(messages))
        return "Please correct the household details before calculation."

    @staticmethod
    def _validation_fields(payload):
        errors = payload.get("errors")
        if not isinstance(errors, list):
            return ("household",)
        fields = tuple(
            error["path"]
            for error in errors
            if isinstance(error, dict)
            and isinstance(error.get("path"), str)
            and error["path"].strip()
        )
        return tuple(dict.fromkeys(fields)) or ("household",)

    @staticmethod
    def _household_revision(candidate):
        payload = candidate.model_dump_json()
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    @classmethod
    def _provenance(cls, context, source):
        return ArtifactProvenance(
            conversation_id=context.conversation_id,
            turn_id=context.turn_id,
            capability_id=cls.spec.identifier,
            capability_version=cls.spec.version,
            invocation_id=context.capability_invocation_id,
            sources=(source,),
        )
