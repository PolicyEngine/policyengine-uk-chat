"""Typed household evidence and deterministic clarification resolution."""

from __future__ import annotations

import re
from decimal import Decimal
from enum import Enum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator
from conversation_context.models import EntityKind, FactRequirement


PersonEvidenceField = Literal[
    "age",
    "employment_income",
    "self_employment_income",
    "pension_income",
]
HouseholdEvidenceField = Literal[
    "is_married",
    "has_children",
    "childcare_expenses",
    "rent",
    "council_tax",
    "country",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AmountFrequency(str, Enum):
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    ANNUAL = "annual"


class PeriodicAmount(StrictModel):
    amount: float
    frequency: AmountFrequency

    def annual_value(self) -> float:
        multiplier = {
            AmountFrequency.WEEKLY: 52,
            AmountFrequency.MONTHLY: 12,
            AmountFrequency.ANNUAL: 1,
        }[self.frequency]
        return self.amount * multiplier


class PersonEvidence(StrictModel):
    entity_id: str | None = None
    display_label: str | None = None
    relationship_to_user: str | None = None
    age: int | None = None
    employment_income: PeriodicAmount | None = None
    self_employment_income: PeriodicAmount | None = None
    pension_income: PeriodicAmount | None = None
    sources: dict[PersonEvidenceField, Literal["user", "artifact", "default"]] = (
        Field(default_factory=dict)
    )


class HouseholdEvidence(StrictModel):
    people: tuple[PersonEvidence, ...] = Field(
        default=(),
        description="Every described adult and child, kept in conversational order.",
    )
    has_children: bool | None = Field(
        default=None,
        description=(
            "True when the current wording says the household has children; false "
            "when it explicitly says there are no children or describes all members "
            "as adults."
        ),
    )
    is_married: bool | None = Field(
        default=None,
        description=(
            "True when multiple adults are described as a couple, married, or in a "
            "civil partnership; false when they are explicitly unrelated adults or "
            "separate benefit units. Null when their relationship is not supplied."
        ),
    )
    childcare_expenses: PeriodicAmount | None = None
    rent: PeriodicAmount | None = None
    council_tax: PeriodicAmount | None = None
    country: Literal["ENGLAND", "SCOTLAND", "WALES", "NORTHERN_IRELAND"] | None = None
    sources: dict[
        HouseholdEvidenceField,
        Literal["user", "artifact", "default"],
    ] = Field(
        default_factory=dict
    )


class HouseholdInvocationDefaults(StrictModel):
    """Invocation-owned values that are reported as defaults, never user facts."""

    evidence: HouseholdEvidence = Field(default_factory=HouseholdEvidence)

    @model_validator(mode="after")
    def only_contains_defaults(self) -> "HouseholdInvocationDefaults":
        for person in self.evidence.people:
            if any(source != "default" for source in person.sources.values()):
                raise ValueError("invocation defaults cannot contain accepted facts")
        if any(source != "default" for source in self.evidence.sources.values()):
            raise ValueError("invocation defaults cannot contain accepted facts")
        return self

    def sterling_amounts(self) -> tuple[float, ...]:
        """Return monetary values introduced by documented invocation defaults."""

        amounts: list[float] = []
        for person in self.evidence.people:
            for field in (
                "employment_income",
                "self_employment_income",
                "pension_income",
            ):
                value = getattr(person, field)
                if value is not None:
                    amounts.extend((value.amount, value.annual_value()))
        for field in ("childcare_expenses", "rent", "council_tax"):
            value = getattr(self.evidence, field)
            if value is not None:
                amounts.extend((value.amount, value.annual_value()))
        return tuple(dict.fromkeys(amounts))


class SterlingMention(StrictModel):
    """One exact monetary mention retained from an authoritative user message."""

    text: str
    amount: Decimal
    message: str


class HouseholdInputCompleteness:
    """Reject household calculations that silently lose monetary input mentions."""

    _sterling_pattern = re.compile(
        r"£\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)"
    )

    def unresolved_mentions(
        self,
        *,
        authoritative_messages: tuple[str, ...],
        verified_amounts: tuple[float, ...],
        excluded_texts: tuple[str, ...] = (),
    ) -> tuple[SterlingMention, ...]:
        verified = {Decimal(str(value)) for value in verified_amounts}
        excluded = {
            Decimal(raw.replace(",", ""))
            for text in excluded_texts
            for raw in self._sterling_pattern.findall(text)
        }
        unresolved: list[SterlingMention] = []
        for message in authoritative_messages:
            for match in self._sterling_pattern.finditer(message):
                amount = Decimal(match.group(1).replace(",", ""))
                if amount in verified or amount in excluded:
                    continue
                mention = SterlingMention(
                    text=match.group(0),
                    amount=amount,
                    message=message,
                )
                if all(
                    item.amount != mention.amount or item.message != mention.message
                    for item in unresolved
                ):
                    unresolved.append(mention)
        return tuple(unresolved)


class HouseholdAssemblerUsage(StrictModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


class HouseholdEvidenceAmbiguityKind(str, Enum):
    INCOME_OWNER = "income_owner"
    INCOME_FREQUENCY = "income_frequency"
    ADULT_RELATIONSHIP = "adult_relationship"
    CONFLICTING_EVIDENCE = "conflicting_evidence"


class HouseholdEvidenceAmbiguity(StrictModel):
    kind: HouseholdEvidenceAmbiguityKind
    field: Literal[
        "employment_income",
        "self_employment_income",
        "pension_income",
        "household_structure",
    ] | None = None
    person_indices: tuple[int, ...] = ()
    amount: float | None = None
    frequency: AmountFrequency | None = None


class HouseholdEvidenceResult(StrictModel):
    evidence: HouseholdEvidence
    ambiguities: tuple[HouseholdEvidenceAmbiguity, ...] = ()
    usage: HouseholdAssemblerUsage = Field(default_factory=HouseholdAssemblerUsage)


class HouseholdEvidenceAssembler(Protocol):
    async def assemble(
        self,
        *,
        description: str,
        retained_evidence: HouseholdEvidence,
        retained_ambiguities: tuple[HouseholdEvidenceAmbiguity, ...],
    ) -> HouseholdEvidenceResult: ...


class HouseholdResolution(StrictModel):
    missing_fields: tuple[str, ...] = ()
    questions: tuple[str, ...] = ()
    fact_requirements: tuple[FactRequirement, ...] = ()


class HouseholdCalculationRequirements(StrictModel):
    """Resolved input requirements for the outputs requested in one calculation."""

    requested_output_ids: tuple[str, ...] = ()
    require_housing_costs: bool = True
    context_scope_id: str = "scope:primary-household"
    household_entity_id: str = "household:primary"


class HouseholdInputResolver:
    """Own deterministic household requirements and comprehensive questions."""

    _first_person_pattern = re.compile(r"\b(?:i|i'm|i am|my|me)\b", re.IGNORECASE)
    _income_word_pattern = re.compile(
        r"\b(?:income|earn|earns|earning|salary|wage|wages)\b",
        re.IGNORECASE,
    )
    _specified_income_kind_pattern = re.compile(
        r"\b(?:self[- ]?employ|pension|dividend|rental|capital gain)",
        re.IGNORECASE,
    )
    _sterling_amount_pattern = re.compile(
        r"£\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)"
    )
    _tax_only_outputs = frozenset(
        {"income_tax", "national_insurance", "household_tax"}
    )

    def apply_documented_defaults(
        self,
        evidence: HouseholdEvidence,
        ambiguities: tuple[HouseholdEvidenceAmbiguity, ...],
        *,
        current_user_message: str,
        requirements: HouseholdCalculationRequirements,
    ) -> tuple[HouseholdEvidence, tuple[HouseholdEvidenceAmbiguity, ...]]:
        """Apply narrow, reportable defaults before deciding what remains missing."""

        output_ids = set(requirements.requested_output_ids)
        if not output_ids or not output_ids.issubset(self._tax_only_outputs):
            return evidence, ambiguities
        if not self._first_person_pattern.search(current_user_message):
            return evidence, ambiguities
        if not self._income_word_pattern.search(current_user_message):
            return evidence, ambiguities
        if self._specified_income_kind_pattern.search(current_user_message):
            return evidence, ambiguities
        matches = self._sterling_amount_pattern.findall(current_user_message)
        if len(matches) != 1 or len(evidence.people) > 1:
            return evidence, ambiguities

        people = list(evidence.people or (PersonEvidence(),))
        person = people[0]
        if any(
            getattr(person, field) is not None
            for field in (
                "employment_income",
                "self_employment_income",
                "pension_income",
            )
        ):
            return evidence, ambiguities
        amount = float(matches[0].replace(",", ""))
        sources = dict(person.sources)
        sources["employment_income"] = "default"
        people[0] = person.model_copy(
            update={
                "employment_income": PeriodicAmount(
                    amount=amount,
                    frequency=AmountFrequency.ANNUAL,
                ),
                "sources": sources,
            }
        )
        remaining_ambiguities = tuple(
            ambiguity
            for ambiguity in ambiguities
            if ambiguity.kind
            not in {
                HouseholdEvidenceAmbiguityKind.INCOME_OWNER,
                HouseholdEvidenceAmbiguityKind.INCOME_FREQUENCY,
            }
        )
        return (
            evidence.model_copy(update={"people": tuple(people)}),
            remaining_ambiguities,
        )

    def resolve(
        self,
        evidence: HouseholdEvidence,
        ambiguities: tuple[HouseholdEvidenceAmbiguity, ...] = (),
        requirements: HouseholdCalculationRequirements | None = None,
    ) -> HouseholdResolution:
        requirements = requirements or HouseholdCalculationRequirements()
        missing_fields: list[str] = []
        questions: list[str] = []

        if not evidence.people:
            missing_fields.append("people")
            questions.append(
                "Who lives in the household? Please give each person's age, say how "
                "multiple adults are related (including whether they form a couple or "
                "civil partnership), and identify which person receives each income "
                "amount and whether it is weekly, monthly, or annual."
            )
        else:
            missing_age_indices = tuple(
                index + 1
                for index, person in enumerate(evidence.people)
                if person.age is None
            )
            if missing_age_indices:
                missing_fields.extend(
                    f"people[{index - 1}].age" for index in missing_age_indices
                )
                people_text = self._natural_list(
                    tuple(
                        self._person_label(evidence.people[index - 1], index)
                        for index in missing_age_indices
                    )
                )
                relationship_text = (
                    " If more than one is an adult, also say whether the adults form "
                    "one couple or civil partnership."
                    if len(evidence.people) > 1 and evidence.is_married is None
                    else ""
                )
                if relationship_text:
                    missing_fields.append("benunit.is_married_if_multiple_adults")
                if len(evidence.people) == 1:
                    age_question = "What age should I use for this calculation?"
                elif len(missing_age_indices) == 1 and people_text == "you":
                    age_question = "What is your age?"
                else:
                    age_question = (
                        f"What is the age of {people_text}?"
                        if len(missing_age_indices) == 1
                        else f"What ages should I use for {people_text}?"
                    )
                questions.append(f"{age_question}{relationship_text}")

            known_adult_count = sum(
                1
                for person in evidence.people
                if person.age is not None and person.age >= 16
            )
            all_ages_known = not missing_age_indices
            if all_ages_known and known_adult_count == 0:
                missing_fields.append("adult")
                questions.append(
                    "The household needs at least one adult. Which person is an adult, "
                    "and what is their age?"
                )
            if (
                known_adult_count > 1
                and evidence.is_married is None
                and not missing_age_indices
            ):
                missing_fields.append("benunit.is_married")
                questions.append("Do the adults form one couple or civil partnership?")
            if evidence.has_children is True and not any(
                person.age is not None and person.age < 16
                for person in evidence.people
            ):
                missing_fields.append("children.ages")
                questions.append("What are the ages of the household's children?")

        for ambiguity in ambiguities:
            if ambiguity.kind is HouseholdEvidenceAmbiguityKind.INCOME_OWNER:
                missing_fields.append("income.owner")
                questions.append("Which person receives each income amount you gave?")
            elif ambiguity.kind is HouseholdEvidenceAmbiguityKind.INCOME_FREQUENCY:
                missing_fields.append("income.frequency")
                questions.append(
                    "For each income amount, is it weekly, monthly, or annual?"
                )
            elif ambiguity.kind is HouseholdEvidenceAmbiguityKind.ADULT_RELATIONSHIP:
                missing_fields.append("benunit.is_married")
                questions.append("Do the adults form one couple or civil partnership?")
            elif ambiguity.kind is HouseholdEvidenceAmbiguityKind.CONFLICTING_EVIDENCE:
                missing_fields.append("conflicting_evidence")
                questions.append(
                    "Which of the conflicting household details should I use?"
                )

        if requirements.require_housing_costs:
            missing_housing_fields: list[str] = []
            if evidence.rent is None:
                missing_fields.append("household.rent")
                missing_housing_fields.append("rent")
            if evidence.council_tax is None:
                missing_fields.append("household.council_tax")
                missing_housing_fields.append("Council Tax")
            if len(missing_housing_fields) == 2:
                questions.append(
                    "Does the household pay rent or Council Tax? For each that applies, "
                    "please give the amount and say whether it is weekly, monthly, or "
                    "annual; otherwise say that it does not apply."
                )
            elif missing_housing_fields:
                cost = missing_housing_fields[0]
                questions.append(
                    f"Does the household pay {cost}? If so, how much, and is that weekly, "
                    "monthly, or annual? Otherwise say that it does not apply."
                )

        return HouseholdResolution(
            missing_fields=tuple(dict.fromkeys(missing_fields)),
            questions=tuple(dict.fromkeys(questions)),
            fact_requirements=self._fact_requirements(
                tuple(dict.fromkeys(missing_fields)),
                evidence,
                requirements,
            ),
        )

    @staticmethod
    def _fact_requirements(
        missing_fields: tuple[str, ...],
        evidence: HouseholdEvidence,
        requirements: HouseholdCalculationRequirements,
    ) -> tuple[FactRequirement, ...]:
        result: list[FactRequirement] = []
        for field in missing_fields:
            fact_key: str
            subject_id: str | None = requirements.household_entity_id
            subject_kind: EntityKind | None = None
            expected = "boolean"
            allow_absence = False
            if field == "people":
                fact_key = "household.members"
                expected = "entity_references"
            elif field.startswith("people[") and field.endswith("].age"):
                try:
                    index = int(field.removeprefix("people[").split("]", 1)[0])
                except ValueError:
                    continue
                if index >= len(evidence.people):
                    continue
                fact_key = "person.age"
                subject_id = evidence.people[index].entity_id
                subject_kind = EntityKind.PERSON if subject_id is None else None
                expected = "integer"
            elif field in {"adult", "children.ages"}:
                fact_key = "person.age"
                subject_id = None
                subject_kind = EntityKind.PERSON
                expected = "integer"
            elif field.startswith("benunit.is_married"):
                fact_key = "household.is_married"
            elif field == "household.rent":
                fact_key = "household.rent"
                expected = "money"
                allow_absence = True
            elif field == "household.council_tax":
                fact_key = "household.council_tax"
                expected = "money"
                allow_absence = True
            elif field in {"income.owner", "income.frequency"}:
                fact_key = "person.employment_income"
                subject_id = None
                subject_kind = EntityKind.PERSON
                expected = "money"
            elif field == "conflicting_evidence":
                continue
            else:
                continue
            result.append(
                FactRequirement(
                    requirement_id=f"household:{field}",
                    fact_key=fact_key,
                    subject_entity_id=subject_id,
                    subject_kind=subject_kind,
                    scope_id=requirements.context_scope_id,
                    expected_value_kind=expected,
                    allow_explicit_absence=allow_absence,
                    reason=f"Required to resolve household input {field}.",
                )
            )
        return tuple(
            {
                (item.fact_key, item.subject_entity_id, item.requirement_id): item
                for item in result
            }.values()
        )

    @staticmethod
    def _natural_list(values: tuple[str, ...]) -> str:
        if len(values) == 1:
            return values[0]
        if len(values) == 2:
            return f"{values[0]} and {values[1]}"
        return f"{', '.join(values[:-1])}, and {values[-1]}"

    @staticmethod
    def _person_label(person: PersonEvidence, position: int) -> str:
        if person.display_label:
            return person.display_label
        if person.relationship_to_user == "self":
            return "you"
        if person.relationship_to_user:
            return f"your {person.relationship_to_user.replace('_', ' ')}"
        return "the other person" if position == 2 else f"person {position}"
