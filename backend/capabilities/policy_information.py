"""Authoritative policy catalogue and calculation-method capability."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue

from capabilities.artifacts import PolicyScenarioRef
from capabilities.contracts import (
    ArtifactContract,
    Capability,
    CapabilitySpec,
    Completed,
    Unsupported,
)
from capabilities.input_resolution import InputSource, resolve_policy_year
from tools.analysis_support import NumericalFact
from tools.contracts import CallerType, Visibility
from tools.typed_models import SafeToolOutput


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PolicyInformationInput(StrictModel):
    question: str
    search_terms: tuple[str, ...] = ()
    year: int | None = None
    referenced_policy_scenario_id: str | None = None
    parameter_path: str | None = None
    variable_name: str | None = None


class PolicyCatalogueFact(StrictModel):
    kind: Literal["parameter", "variable"]
    identifier: str
    label: str | None = None
    description: str | None = None
    unit: str | None = None
    entity: str | None = None
    definition_period: str | None = None
    quantity_type: str | None = None
    reference: JsonValue | None = None
    defined_for: str | None = None
    min_value: int | float | None = None
    max_value: int | float | None = None
    is_period_size_independent: bool | None = None
    metadata: JsonValue | None = None
    value: JsonValue | None = None
    source_tool: str


class PolicyInformationOutput(StrictModel):
    year: int
    year_source: InputSource
    facts: tuple[PolicyCatalogueFact, ...]
    source_context: tuple[str, ...]
    limitations: tuple[str, ...] = ()
    narration_facts: tuple[NumericalFact, ...] = ()


class PolicyInformationCapability(
    Capability[PolicyInformationInput, PolicyInformationOutput]
):
    spec = CapabilitySpec(
        identifier="policy_information",
        version="1",
        description=(
            "Retrieve authoritative UK policy parameters, variables, scope, and "
            "calculation-method metadata from ordinary language or exact identifiers."
        ),
        required_use=(
            "Required for questions about how a government policy value is formulated, "
            "scoped, or calculated."
        ),
        visibility=Visibility.PUBLIC,
        allowed_callers=frozenset({CallerType.MODEL, CallerType.CAPABILITY}),
        input_model=PolicyInformationInput,
        output_model=PolicyInformationOutput,
        accepted_artifacts=(
            ArtifactContract(artifact_type="policy_scenario", schema_version="1"),
        ),
        tool_dependencies=(
            "search_parameters",
            "get_parameter",
            "search_variables",
            "get_variable",
        ),
    )

    async def run(self, capability_input: PolicyInformationInput, context):
        referenced_year = await self._referenced_year(capability_input, context)
        resolved_year = resolve_policy_year(
            explicit_year=capability_input.year,
            referenced_year=referenced_year,
        )
        facts: list[PolicyCatalogueFact] = []
        sources: list[str] = []

        if capability_input.parameter_path:
            result = await context.invoke_tool(
                "get_parameter",
                {
                    "path": capability_input.parameter_path,
                    "year": resolved_year.year,
                },
            )
            facts.extend(self._parameter_facts(result, "get_parameter"))
            sources.append("get_parameter")
        if capability_input.variable_name:
            result = await context.invoke_tool(
                "get_variable",
                {"name": capability_input.variable_name},
            )
            facts.extend(self._variable_facts(result, "get_variable"))
            sources.append("get_variable")

        for query in self._queries(capability_input):
            if not capability_input.parameter_path:
                result = await context.invoke_tool(
                    "search_parameters",
                    {"query": query, "limit": 10},
                )
                facts.extend(self._parameter_facts(result, "search_parameters"))
                sources.append("search_parameters")
            if not capability_input.variable_name:
                result = await context.invoke_tool(
                    "search_variables",
                    {"query": query, "limit": 10},
                )
                facts.extend(self._variable_facts(result, "search_variables"))
                sources.append("search_variables")
            if facts:
                break

        facts = list(
            {
                (fact.kind, fact.identifier): fact
                for fact in facts
            }.values()
        )
        if not facts:
            return Unsupported(
                reason=(
                    "The authoritative policy catalogue did not return enough "
                    "information to support this explanation."
                )
            )
        numeric = tuple(
            NumericalFact(
                label=fact.label or fact.identifier,
                value=fact.value,
                unit=fact.unit or "value",
            )
            for fact in facts
            if fact.kind == "parameter"
            and fact.source_tool == "get_parameter"
            and isinstance(fact.value, (int, float))
            and not isinstance(fact.value, bool)
        )
        return Completed(
            value=PolicyInformationOutput(
                year=resolved_year.year,
                year_source=resolved_year.source,
                facts=tuple(facts),
                source_context=tuple(sources),
                narration_facts=numeric,
            )
        )

    @staticmethod
    async def _referenced_year(capability_input, context) -> int | None:
        reference = capability_input.referenced_policy_scenario_id
        if reference is None:
            return None
        scenarios = await context.find_artifacts(PolicyScenarioRef)
        scenario = next(
            (item for item in scenarios if item.artifact_id == reference),
            None,
        )
        return scenario.year if scenario is not None else None

    @staticmethod
    def _payload(result, expected: str) -> dict[str, JsonValue]:
        if not isinstance(result, SafeToolOutput):
            raise TypeError(f"{expected} returned an incompatible result.")
        return result.root

    @staticmethod
    def _queries(capability_input: PolicyInformationInput) -> tuple[str, ...]:
        if capability_input.search_terms:
            return tuple(dict.fromkeys(capability_input.search_terms))[:4]
        tokens = [
            token.strip(".,?!:;()[]{}\"'")
            for token in capability_input.question.casefold().split()
        ]
        stop = {
            "how",
            "is",
            "are",
            "a",
            "an",
            "the",
            "for",
            "of",
            "to",
            "value",
            "amount",
            "calculated",
            "determined",
        }
        significant = [token for token in tokens if token and token not in stop]
        phrases = [capability_input.question]
        phrases.extend(
            " ".join(significant[index : index + size])
            for size in (3, 2)
            for index in range(max(0, len(significant) - size + 1))
        )
        return tuple(dict.fromkeys(phrases))[:4]

    @classmethod
    def _parameter_facts(cls, result, source) -> list[PolicyCatalogueFact]:
        payload = cls._payload(result, source)
        rows = payload.get("parameters")
        if rows is None and isinstance(payload.get("parameter"), dict):
            rows = [payload["parameter"]]
        if not isinstance(rows, list):
            return []
        return [
            PolicyCatalogueFact(
                kind="parameter",
                identifier=row["path"],
                label=row.get("label"),
                description=row.get("description"),
                unit=row.get("unit"),
                value=row.get("value"),
                source_tool=source,
            )
            for row in rows
            if isinstance(row, dict) and isinstance(row.get("path"), str)
        ]

    @classmethod
    def _variable_facts(cls, result, source) -> list[PolicyCatalogueFact]:
        payload = cls._payload(result, source)
        rows = payload.get("variables")
        if rows is None and isinstance(payload.get("variable"), dict):
            rows = [payload["variable"]]
        if not isinstance(rows, list):
            return []
        facts: list[PolicyCatalogueFact] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            identifier = row.get("name")
            if not isinstance(identifier, str):
                continue
            facts.append(
                PolicyCatalogueFact(
                    kind="variable",
                    identifier=identifier,
                    label=cls._optional_string(row.get("label")),
                    description=cls._optional_string(row.get("description")),
                    unit=cls._optional_string(row.get("unit")),
                    entity=cls._optional_string(row.get("entity")),
                    definition_period=cls._optional_string(
                        row.get("definition_period")
                    ),
                    quantity_type=cls._optional_string(row.get("quantity_type")),
                    reference=row.get("reference"),
                    defined_for=cls._optional_string(row.get("defined_for")),
                    min_value=cls._optional_number(row.get("min_value")),
                    max_value=cls._optional_number(row.get("max_value")),
                    is_period_size_independent=cls._optional_bool(
                        row.get("is_period_size_independent")
                    ),
                    metadata=row.get("metadata"),
                    value=row.get("default_value"),
                    source_tool=source,
                )
            )
        return facts

    @staticmethod
    def _optional_string(value: JsonValue | None) -> str | None:
        return value if isinstance(value, str) else None

    @staticmethod
    def _optional_number(value: JsonValue | None) -> int | float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return value

    @staticmethod
    def _optional_bool(value: JsonValue | None) -> bool | None:
        return value if isinstance(value, bool) else None
