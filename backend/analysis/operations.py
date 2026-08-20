"""Immutable operation, producer, and chart authority for analysis compilation."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType
from typing import Iterable, Mapping

from analysis.capabilities import (
    CAPABILITY_REGISTRY,
    CapabilityRegistry,
    OutputProducer,
)
from analysis.common import AnalysisError, AnalysisErrorCode
from tools.definitions import CHART_PRESET_SOURCES
from tools.registry import RegisteredTool, tool_specs


@dataclass(frozen=True)
class ChartRecipe:
    chart_kind: str
    source_output: str
    source_operation: str
    source_result_type: str
    chart_operation: str = "generate_chart"
    source_argument: str = "result_id"


@dataclass(frozen=True)
class OperationCatalogue:
    capability_version: str
    operations: Mapping[str, RegisteredTool]
    producers: Mapping[str, OutputProducer]
    chart_recipes: Mapping[str, ChartRecipe]

    def operation(self, name: str) -> RegisteredTool:
        operation = self.operations.get(name)
        if operation is None:
            raise AnalysisError(
                AnalysisErrorCode.CAPABILITY_INVALID,
                f"operation {name!r} is not registered",
            )
        return operation

    def chart_recipe(self, chart_kind: str) -> ChartRecipe:
        recipe = self.chart_recipes.get(chart_kind)
        if recipe is None:
            raise AnalysisError(
                AnalysisErrorCode.CAPABILITY_INVALID,
                f"chart kind {chart_kind!r} has no registered recipe",
            )
        return recipe

    def tool_definitions(self) -> tuple[dict[str, object], ...]:
        """Return caller-owned model definitions without exposing catalogue state."""

        return tuple(
            {
                "name": operation.name,
                "description": operation.description,
                "input_schema": deepcopy(operation.input_schema),
            }
            for operation in self.operations.values()
        )

    def validate(self, registry: CapabilityRegistry) -> None:
        errors: list[str] = []
        if self.capability_version != registry.version:
            errors.append(
                "operation catalogue capability version differs from the registry"
            )
        registry.validate(self.operations)
        for name, operation in self.operations.items():
            if name != operation.name:
                errors.append(f"operation key {name} disagrees with its identifier")
            if (
                getattr(operation, "input_adapter", None) is None
                or getattr(operation, "output_adapter", None) is None
            ):
                errors.append(f"operation {name} is missing a typed adapter")
            if not operation.result_type:
                errors.append(f"operation {name} is missing its result type")
            if getattr(operation, "handler", None) is None:
                errors.append(f"operation {name} is missing its dispatch adapter")
            if getattr(operation, "fact_extractor", None) is None:
                errors.append(f"operation {name} is missing its fact extractor")
            if getattr(operation, "public_summary_builder", None) is None:
                errors.append(f"operation {name} is missing its public summary builder")
        for output, producer in self.producers.items():
            if registry.producers.get(output) != producer:
                errors.append(f"producer {output} differs from the capability registry")
        chart_producer = self.producers.get("chart")
        if chart_producer is None:
            errors.append("chart output has no producer")
        for chart_kind, recipe in self.chart_recipes.items():
            if chart_kind != recipe.chart_kind:
                errors.append(
                    f"chart recipe key {chart_kind} disagrees with its chart kind"
                )
            source = self.producers.get(recipe.source_output)
            if source is None:
                errors.append(
                    f"chart recipe {chart_kind} names missing output "
                    f"{recipe.source_output}"
                )
                continue
            if source.operation != recipe.source_operation:
                errors.append(
                    f"chart recipe {chart_kind} source operation differs from its producer"
                )
            if source.result_type != recipe.source_result_type:
                errors.append(
                    f"chart recipe {chart_kind} source result type differs from its producer"
                )
            source_operation = self.operations.get(recipe.source_operation)
            chart_operation = self.operations.get(recipe.chart_operation)
            if source_operation is None:
                errors.append(
                    f"chart recipe {chart_kind} names missing source operation"
                )
            elif source_operation.result_type != recipe.source_result_type:
                errors.append(
                    f"chart recipe {chart_kind} source operation returns another type"
                )
            if chart_operation is None:
                errors.append(
                    f"chart recipe {chart_kind} names missing chart operation"
                )
            else:
                properties = chart_operation.input_schema.get("properties", {})
                if recipe.source_argument not in properties:
                    errors.append(
                        f"chart recipe {chart_kind} names an unknown source argument"
                    )
                if (
                    recipe.source_result_type
                    not in chart_operation.permitted_dependency_types
                ):
                    errors.append(
                        f"chart recipe {chart_kind} uses a prohibited dependency type"
                    )
        if errors:
            raise AnalysisError(
                AnalysisErrorCode.CAPABILITY_INVALID,
                "; ".join(errors),
            )


_CHART_RECIPES = tuple(
    ChartRecipe(chart_kind, *source)
    for chart_kind, source in CHART_PRESET_SOURCES.items()
)


def build_operation_catalogue(
    registry: CapabilityRegistry = CAPABILITY_REGISTRY,
    specifications: Iterable[RegisteredTool] | None = None,
) -> OperationCatalogue:
    supplied = tuple(specifications if specifications is not None else tool_specs())
    operations = {operation.name: operation for operation in supplied}
    if len(operations) != len(supplied):
        raise AnalysisError(
            AnalysisErrorCode.CAPABILITY_INVALID,
            "operation catalogue contains duplicate identifiers",
        )
    catalogue = OperationCatalogue(
        capability_version=registry.version,
        operations=MappingProxyType(operations),
        producers=MappingProxyType(dict(registry.producers)),
        chart_recipes=MappingProxyType(
            {recipe.chart_kind: recipe for recipe in _CHART_RECIPES}
        ),
    )
    catalogue.validate(registry)
    return catalogue


@lru_cache(maxsize=1)
def default_operation_catalogue() -> OperationCatalogue:
    return build_operation_catalogue()
