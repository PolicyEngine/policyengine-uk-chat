"""Project accepted engine-backed facts into deterministic calculation inputs."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from conversation_context.models import (
    BooleanFactValue,
    ConversationContext,
    FactValue,
    IntegerFactValue,
    MoneyFactValue,
    PresentAssertion,
    TextFactValue,
)
from conversation_context.registry import FactDefinitionRegistry


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PersonEngineInputs(StrictModel):
    entity_id: str
    values: dict[str, JsonValue]


class HouseholdEngineInputs(StrictModel):
    people: tuple[PersonEngineInputs, ...] = ()
    benunit: dict[str, JsonValue] = Field(default_factory=dict)
    household: dict[str, JsonValue] = Field(default_factory=dict)


class HouseholdEngineFactProjector:
    """Map registered PolicyEngine bindings without enumerating variable names."""

    def __init__(self, registry: FactDefinitionRegistry) -> None:
        self._registry = registry

    def project(
        self,
        context: ConversationContext,
        *,
        scope_id: str,
        person_entity_ids: tuple[str, ...],
        household_entity_id: str,
    ) -> HouseholdEngineInputs:
        self._registry.restore_engine_definitions(context)
        people: dict[str, dict[str, JsonValue]] = {
            entity_id: {} for entity_id in person_entity_ids
        }
        benunit: dict[str, JsonValue] = {}
        household: dict[str, JsonValue] = {}
        for fact in context.active_facts():
            if fact.scope_id != scope_id or not isinstance(
                fact.assertion,
                PresentAssertion,
            ):
                continue
            try:
                definition = self._registry.get(
                    fact.definition_key,
                    fact.definition_version,
                )
            except KeyError:
                continue
            binding = definition.engine_binding
            if binding is None or "." not in binding:
                continue
            engine_entity, variable_name = binding.split(".", 1)
            value = self._value(fact.assertion.value)
            if value is None:
                continue
            if engine_entity == "person" and fact.subject_entity_id in people:
                people[fact.subject_entity_id][variable_name] = value
            elif (
                engine_entity == "benunit"
                and fact.subject_entity_id == household_entity_id
            ):
                benunit[variable_name] = value
            elif (
                engine_entity == "household"
                and fact.subject_entity_id == household_entity_id
            ):
                household[variable_name] = value
        return HouseholdEngineInputs(
            people=tuple(
                PersonEngineInputs(entity_id=entity_id, values=people[entity_id])
                for entity_id in person_entity_ids
            ),
            benunit=benunit,
            household=household,
        )

    @staticmethod
    def _value(value: FactValue) -> JsonValue | None:
        if isinstance(value, BooleanFactValue):
            return value.value
        if isinstance(value, IntegerFactValue):
            return value.value
        if isinstance(value, MoneyFactValue):
            return float(value.amount)
        if isinstance(value, TextFactValue):
            return value.value
        return None
