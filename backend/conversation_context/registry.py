"""Registered definitions for facts the current runtime understands."""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from conversation_context.models import (
    ConversationContext,
    EntityKind,
    FactValue,
    MoneyFactValue,
    PresentAssertion,
)


class FactValueKind(str, Enum):
    BOOLEAN = "boolean"
    INTEGER = "integer"
    MONEY = "money"
    TEXT = "text"
    TEXT_SET = "text_set"
    ENTITY_REFERENCE = "entity_reference"
    ENTITY_REFERENCES = "entity_references"


class TemporalSemantics(str, Enum):
    CURRENT = "current"
    AS_OF_YEAR = "as_of_year"
    SCENARIO = "scenario"


class FactUpdatePolicy(str, Enum):
    REQUIRE_EXPLICIT_CORRECTION = "require_explicit_correction"
    REPLACE_ON_EXPLICIT_ASSERTION = "replace_on_explicit_assertion"


class FactDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    version: str = "1"
    value_kind: FactValueKind
    subject_kinds: frozenset[EntityKind]
    cardinality: str = "one_per_subject_scope"
    temporal_semantics: TemporalSemantics = TemporalSemantics.CURRENT
    update_policy: FactUpdatePolicy = FactUpdatePolicy.REQUIRE_EXPLICIT_CORRECTION
    label: str
    sensitivity: str = "personal"
    allow_explicit_absence: bool = False
    allowed_text_values: tuple[str, ...] = ()
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    engine_binding: str | None = None

    @field_validator("key", "version", "label", "cardinality", "sensitivity")
    @classmethod
    def non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value

    @model_validator(mode="after")
    def validate_definition(self) -> "FactDefinition":
        if not self.subject_kinds:
            raise ValueError("at least one subject kind is required")
        if self.allowed_text_values and self.value_kind is not FactValueKind.TEXT:
            raise ValueError("allowed_text_values require a text fact")
        return self

    def validate_value(self, value: FactValue) -> str | None:
        if value.kind != self.value_kind.value:
            return f"expected {self.value_kind.value} value, received {value.kind}"
        scalar: Decimal | None = None
        if isinstance(value, MoneyFactValue):
            scalar = value.amount
        elif value.kind == "integer":
            scalar = Decimal(value.value)
        if scalar is not None and self.minimum is not None and scalar < self.minimum:
            return f"value must be at least {self.minimum}"
        if scalar is not None and self.maximum is not None and scalar > self.maximum:
            return f"value must be at most {self.maximum}"
        if value.kind == "text" and self.allowed_text_values:
            if value.value not in self.allowed_text_values:
                return "value is not in the registered allowed set"
        return None


class FactDefinitionRegistry:
    def __init__(self, definitions: tuple[FactDefinition, ...] = ()) -> None:
        self._definitions: dict[tuple[str, str], FactDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: FactDefinition) -> None:
        identity = (definition.key, definition.version)
        if identity in self._definitions:
            raise ValueError(
                f"Duplicate fact definition: {definition.key}@{definition.version}"
            )
        self._definitions[identity] = definition

    def get(self, key: str, version: str = "1") -> FactDefinition:
        try:
            return self._definitions[(key, version)]
        except KeyError as exc:
            raise KeyError(f"Unknown fact definition: {key}@{version}") from exc

    def find_by_engine_binding(
        self,
        variable_name: str,
        *,
        entity: str | None = None,
    ) -> FactDefinition | None:
        """Return a definition backed by one exact PolicyEngine variable."""

        matches: list[FactDefinition] = []
        for definition in self._definitions.values():
            binding = definition.engine_binding
            if binding is None:
                continue
            binding_parts = binding.split(".", 1)
            binding_entity = binding_parts[0] if len(binding_parts) == 2 else None
            binding_name = binding_parts[-1]
            if binding_name != variable_name:
                continue
            if entity is not None and binding_entity not in {None, entity}:
                continue
            matches.append(definition)
        if len(matches) == 1:
            return matches[0]
        return None

    def ensure_engine_definition(
        self,
        *,
        variable_name: str,
        entity: str,
        label: str,
        value_kind: FactValueKind,
    ) -> FactDefinition:
        """Materialize registry metadata from one verified catalogue record."""

        existing = self.find_by_engine_binding(variable_name, entity=entity)
        if existing is not None:
            return existing
        subject_kind = {
            "person": EntityKind.PERSON,
            "household": EntityKind.HOUSEHOLD,
            "benunit": EntityKind.HOUSEHOLD,
        }.get(entity)
        if subject_kind is None:
            raise ValueError(
                f"PolicyEngine entity {entity!r} has no conversation-entity mapping."
            )
        definition = FactDefinition(
            key=f"pe.{entity}.{variable_name}",
            value_kind=value_kind,
            subject_kinds=frozenset({subject_kind}),
            label=label,
            engine_binding=f"{entity}.{variable_name}",
        )
        self.register(definition)
        return definition

    def restore_engine_definitions(self, context: ConversationContext) -> None:
        """Restore verified generated definitions referenced by persisted facts."""

        for fact in context.facts:
            try:
                self.get(fact.definition_key, fact.definition_version)
                continue
            except KeyError:
                pass
            parts = fact.definition_key.split(".", 2)
            if (
                len(parts) != 3
                or parts[0] != "pe"
                or fact.definition_version != "1"
                or not isinstance(fact.assertion, PresentAssertion)
            ):
                continue
            entity, variable_name = parts[1:]
            try:
                value_kind = FactValueKind(fact.assertion.value.kind)
            except ValueError:
                continue
            self.ensure_engine_definition(
                variable_name=variable_name,
                entity=entity,
                label=variable_name.replace("_", " ").title(),
                value_kind=value_kind,
            )

    def definitions(self) -> tuple[FactDefinition, ...]:
        return tuple(self._definitions.values())

    def model_projection(self) -> tuple[dict[str, object], ...]:
        return tuple(
            definition.model_dump(mode="json", exclude_none=True)
            for definition in self.definitions()
        )


def _fact(
    key: str,
    value_kind: FactValueKind,
    subjects: frozenset[EntityKind],
    label: str,
    **kwargs: Any,
) -> FactDefinition:
    return FactDefinition(
        key=key,
        value_kind=value_kind,
        subject_kinds=subjects,
        label=label,
        **kwargs,
    )


def build_default_fact_registry() -> FactDefinitionRegistry:
    person = frozenset({EntityKind.PERSON})
    household = frozenset({EntityKind.HOUSEHOLD})
    scenario = frozenset({EntityKind.POLICY_SCENARIO})
    non_negative = {"minimum": Decimal("0")}
    return FactDefinitionRegistry(
        (
            _fact(
                "person.name",
                FactValueKind.TEXT,
                person,
                "Name",
                engine_binding=None,
            ),
            _fact(
                "person.age",
                FactValueKind.INTEGER,
                person,
                "Age",
                minimum=Decimal("0"),
                maximum=Decimal("120"),
                engine_binding="person.age",
            ),
            _fact(
                "person.employment_income",
                FactValueKind.MONEY,
                person,
                "Employment income",
                **non_negative,
                engine_binding="person.employment_income",
            ),
            _fact(
                "person.self_employment_income",
                FactValueKind.MONEY,
                person,
                "Self-employment income",
                **non_negative,
                engine_binding="person.self_employment_income",
            ),
            _fact(
                "person.pension_income",
                FactValueKind.MONEY,
                person,
                "Pension income",
                **non_negative,
                engine_binding="person.pension_income",
            ),
            _fact(
                "person.childcare_expenses",
                FactValueKind.MONEY,
                person,
                "Childcare expenses",
                allow_explicit_absence=True,
                **non_negative,
                engine_binding="person.childcare_expenses",
            ),
            _fact(
                "person.medical_expenses",
                FactValueKind.MONEY,
                person,
                "Medical expenses",
                allow_explicit_absence=True,
                **non_negative,
            ),
            _fact(
                "household.members",
                FactValueKind.ENTITY_REFERENCES,
                household,
                "Household members",
            ),
            _fact(
                "household.is_married",
                FactValueKind.BOOLEAN,
                household,
                "Married or in a civil partnership",
                engine_binding="benunit.is_married",
            ),
            _fact(
                "household.has_children",
                FactValueKind.BOOLEAN,
                household,
                "Has children",
            ),
            _fact(
                "household.rent",
                FactValueKind.MONEY,
                household,
                "Rent",
                allow_explicit_absence=True,
                **non_negative,
                engine_binding="household.rent",
            ),
            _fact(
                "household.council_tax",
                FactValueKind.MONEY,
                household,
                "Council Tax",
                allow_explicit_absence=True,
                **non_negative,
                engine_binding="household.council_tax",
            ),
            _fact(
                "household.country",
                FactValueKind.TEXT,
                household,
                "Country within the UK",
                allowed_text_values=(
                    "ENGLAND",
                    "SCOTLAND",
                    "WALES",
                    "NORTHERN_IRELAND",
                ),
                engine_binding="household.country",
            ),
            _fact(
                "analysis.policy_year",
                FactValueKind.INTEGER,
                frozenset({EntityKind.HOUSEHOLD, EntityKind.POLICY_SCENARIO}),
                "Policy year",
                minimum=Decimal("2000"),
                maximum=Decimal("2100"),
                temporal_semantics=TemporalSemantics.AS_OF_YEAR,
            ),
            _fact(
                "analysis.requested_outputs",
                FactValueKind.TEXT_SET,
                frozenset({EntityKind.HOUSEHOLD, EntityKind.POLICY_SCENARIO}),
                "Requested calculation outputs",
                update_policy=FactUpdatePolicy.REPLACE_ON_EXPLICIT_ASSERTION,
            ),
            _fact(
                "policy.reform_instruction",
                FactValueKind.TEXT,
                scenario,
                "Policy reform instruction",
                temporal_semantics=TemporalSemantics.SCENARIO,
            ),
        )
    )
