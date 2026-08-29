"""Projection from registered conversation facts to household input evidence."""

from __future__ import annotations

from decimal import Decimal

from capabilities.household_input import (
    AmountFrequency,
    HouseholdEvidence,
    PeriodicAmount,
    PersonEvidence,
)
from conversation_context.models import (
    BooleanFactValue,
    ConversationContext,
    EntityKind,
    EntityReferencesFactValue,
    ExplicitAbsenceAssertion,
    IntegerFactValue,
    MoneyFactValue,
    MoneyPeriod,
    PendingFactResolution,
    PresentAssertion,
    TextFactValue,
    TextSetFactValue,
    FactAssertion,
)


class HouseholdContextView:
    """Read only active facts from one stable household scope."""

    def __init__(self, context: ConversationContext) -> None:
        self._context = context

    @property
    def scope_id(self) -> str:
        if self._context.focus.scope_id is not None:
            return self._context.focus.scope_id
        return next(scope.scope_id for scope in self._context.scopes if scope.active)

    def evidence(self) -> HouseholdEvidence:
        scope = next(
            scope for scope in self._context.scopes if scope.scope_id == self.scope_id
        )
        household = next(
            entity
            for entity in self._context.entities
            if entity.kind is EntityKind.HOUSEHOLD
            and entity.entity_id in scope.subject_entity_ids
        )
        member_ids = self._member_ids(household.entity_id, scope.subject_entity_ids)
        values: dict[str, object] = {
            "people": tuple(self._person_evidence(item) for item in member_ids)
        }
        sources: dict[str, str] = {}
        for fact_key, field in (
            ("household.has_children", "has_children"),
            ("household.is_married", "is_married"),
            ("household.rent", "rent"),
            ("household.council_tax", "council_tax"),
            ("household.country", "country"),
        ):
            fact = self._context.active_fact(fact_key, household.entity_id, self.scope_id)
            if fact is None:
                continue
            converted = self._household_value(fact.assertion)
            if converted is None and not isinstance(
                fact.assertion,
                ExplicitAbsenceAssertion,
            ):
                continue
            values[field] = converted
            sources[field] = "user"

        childcare = next(
            (
                fact
                for entity_id in member_ids
                if (
                    fact := self._context.active_fact(
                        "person.childcare_expenses",
                        entity_id,
                        self.scope_id,
                    )
                )
                is not None
            ),
            None,
        )
        if childcare is not None:
            values["childcare_expenses"] = self._household_value(
                childcare.assertion
            )
            sources["childcare_expenses"] = "user"
        values["sources"] = sources
        return HouseholdEvidence.model_validate(values)

    def policy_year(self) -> int | None:
        fact = self._context.active_fact(
            "analysis.policy_year",
            self.household_entity_id,
            self.scope_id,
        )
        if (
            fact is not None
            and isinstance(fact.assertion, PresentAssertion)
            and isinstance(fact.assertion.value, IntegerFactValue)
        ):
            return fact.assertion.value.value
        return None

    def requested_outputs(self) -> tuple[str, ...]:
        fact = self._context.active_fact(
            "analysis.requested_outputs",
            self.household_entity_id,
            self.scope_id,
        )
        if (
            fact is not None
            and isinstance(fact.assertion, PresentAssertion)
            and isinstance(fact.assertion.value, TextSetFactValue)
        ):
            return fact.assertion.value.values
        return ()

    def pending_fact_resolutions(self) -> tuple[PendingFactResolution, ...]:
        """Return unresolved variable proposals that affect this household scope."""

        scope = next(
            item for item in self._context.scopes if item.scope_id == self.scope_id
        )
        subjects = set(scope.subject_entity_ids)
        return tuple(
            proposal
            for proposal in self._context.pending_fact_resolutions
            if proposal.scope_id == self.scope_id
            and bool(set(proposal.referenced_entity_ids) & subjects)
        )

    @property
    def person_entity_ids(self) -> tuple[str, ...]:
        """Return stable person identifiers in household calculation order."""

        scope = next(
            scope for scope in self._context.scopes if scope.scope_id == self.scope_id
        )
        return self._member_ids(self.household_entity_id, scope.subject_entity_ids)

    @property
    def household_entity_id(self) -> str:
        scope = next(
            scope for scope in self._context.scopes if scope.scope_id == self.scope_id
        )
        return next(
            entity.entity_id
            for entity in self._context.entities
            if entity.kind is EntityKind.HOUSEHOLD
            and entity.entity_id in scope.subject_entity_ids
        )

    def _member_ids(
        self,
        household_id: str,
        scope_subjects: tuple[str, ...],
    ) -> tuple[str, ...]:
        membership = self._context.active_fact(
            "household.members",
            household_id,
            self.scope_id,
        )
        if (
            membership is not None
            and isinstance(membership.assertion, PresentAssertion)
            and isinstance(
                membership.assertion.value,
                EntityReferencesFactValue,
            )
        ):
            candidates = membership.assertion.value.entity_ids
        else:
            candidates = scope_subjects
        people = [
            entity
            for entity in self._context.entities
            if entity.kind is EntityKind.PERSON and entity.entity_id in candidates
        ]
        return tuple(
            entity.entity_id
            for entity in sorted(
                people,
                key=lambda item: (
                    item.relationship_to_user != "self",
                    item.created_turn_id or "",
                    item.entity_id,
                ),
            )
        )

    def _person_evidence(self, entity_id: str) -> PersonEvidence:
        entity = next(
            item for item in self._context.entities if item.entity_id == entity_id
        )
        display_label = self._display_label(entity_id, entity.relationship_to_user)
        values: dict[str, object] = {
            "entity_id": entity_id,
            "display_label": display_label,
            "relationship_to_user": entity.relationship_to_user,
        }
        sources: dict[str, str] = {}
        for fact_key, field in (
            ("person.age", "age"),
            ("person.employment_income", "employment_income"),
            ("person.self_employment_income", "self_employment_income"),
            ("person.pension_income", "pension_income"),
        ):
            fact = self._context.active_fact(fact_key, entity_id, self.scope_id)
            if fact is None or not isinstance(fact.assertion, PresentAssertion):
                continue
            value = fact.assertion.value
            if isinstance(value, IntegerFactValue):
                values[field] = value.value
            elif isinstance(value, MoneyFactValue):
                values[field] = self._periodic(value)
            else:
                continue
            sources[field] = "user"
        values["sources"] = sources
        return PersonEvidence.model_validate(values)

    def _display_label(
        self,
        entity_id: str,
        relationship: str | None,
    ) -> str:
        name = self._context.active_fact(
            "person.name",
            entity_id,
            self.scope_id,
        )
        if (
            name is not None
            and isinstance(name.assertion, PresentAssertion)
            and isinstance(name.assertion.value, TextFactValue)
        ):
            return name.assertion.value.value
        if relationship == "self":
            return "you"
        if relationship:
            return f"your {relationship.replace('_', ' ')}"
        return "the other person"

    @staticmethod
    def _household_value(
        assertion: FactAssertion,
    ) -> bool | str | PeriodicAmount | None:
        if isinstance(assertion, ExplicitAbsenceAssertion):
            return PeriodicAmount(amount=0, frequency=AmountFrequency.ANNUAL)
        if not isinstance(assertion, PresentAssertion):
            return None
        value = assertion.value
        if isinstance(value, BooleanFactValue):
            return value.value
        if isinstance(value, MoneyFactValue):
            return HouseholdContextView._periodic(value)
        if isinstance(value, TextFactValue):
            return value.value
        return None

    @staticmethod
    def _periodic(value: MoneyFactValue) -> PeriodicAmount:
        if value.period is MoneyPeriod.WEEKLY:
            return PeriodicAmount(
                amount=float(value.amount),
                frequency=AmountFrequency.WEEKLY,
            )
        if value.period is MoneyPeriod.MONTHLY:
            return PeriodicAmount(
                amount=float(value.amount),
                frequency=AmountFrequency.MONTHLY,
            )
        annual = (
            value.amount * Decimal(13)
            if value.period is MoneyPeriod.FOUR_WEEKLY
            else value.amount
        )
        return PeriodicAmount(
            amount=float(annual),
            frequency=AmountFrequency.ANNUAL,
        )
