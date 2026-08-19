"""Server-owned semantic fields, analysis capabilities, and output producers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Any, Literal, Mapping

from pydantic import Field, StrictBool, StrictFloat, StrictInt, StrictStr, TypeAdapter

from analysis.common import AnalysisError, AnalysisErrorCode
from analysis.models import REFORM_INSTRUCTION_ADAPTER


CAPABILITY_VERSION = "2"

ANALYSIS_KINDS = (
    "explanation",
    "parameter_lookup",
    "reform_validation",
    "household",
    "society",
    "exploratory",
)


class EvidencePolicy(StrEnum):
    EXACT = "exact"
    CONTROLLED = "controlled"
    STRUCTURED = "structured"
    NARRATIVE = "narrative"
    NONE = "none"


class CapabilityExecutionMode(StrEnum):
    EXPLANATION = "explanation"
    STANDARD = "standard"
    EXPLORATORY = "exploratory"


@dataclass(frozen=True)
class SemanticFieldSpec:
    name: str
    adapter: TypeAdapter[Any]
    analysis_kinds: frozenset[str]
    evidence_policy: EvidencePolicy
    allow_set: bool = True
    allow_clear: bool = True
    clarification_contract: str | None = None
    controlled_values: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def validate(self, value: Any) -> Any:
        try:
            return self.adapter.validate_python(value)
        except ValueError as exc:
            raise AnalysisError(
                AnalysisErrorCode.INVALID_CANDIDATE_TYPE,
                f"semantic field {self.name} has an invalid value",
            ) from exc


@dataclass(frozen=True)
class OutputProducer:
    producer_id: str
    output: str
    analysis_kinds: frozenset[str]
    result_type: str
    operation: str
    operation_arguments: frozenset[str]
    required_fields: tuple[str, ...] = ()
    prerequisite: str | None = None
    source_output: str | None = None


@dataclass(frozen=True)
class ExploratoryProfile:
    operations: tuple[str, ...]
    permitted_dependencies: Mapping[str, tuple[str, ...]]
    permitted_dependency_types: Mapping[str, tuple[str, ...]]
    max_model_iterations: int
    max_operation_calls: int


@dataclass(frozen=True)
class AnalysisCapability:
    analysis_kind: str
    semantic_fields: frozenset[str]
    required_fields: tuple[str, ...]
    optional_fields: tuple[str, ...]
    defaults: Mapping[str, Any]
    supported_outputs: frozenset[str]
    default_outputs: tuple[str, ...]
    execution_mode: CapabilityExecutionMode
    standard_template: str | None = None
    exploratory_profile: ExploratoryProfile | None = None


@dataclass(frozen=True)
class CapabilityRegistry:
    version: str
    fields: Mapping[str, SemanticFieldSpec]
    capabilities: Mapping[str, AnalysisCapability]
    producers: Mapping[str, OutputProducer]

    def field_for(self, name: str, analysis_kind: str) -> SemanticFieldSpec:
        spec = self.fields.get(name)
        if spec is None or analysis_kind not in spec.analysis_kinds:
            raise AnalysisError(
                AnalysisErrorCode.INVALID_CANDIDATE,
                f"semantic field {name} is not permitted for {analysis_kind}",
            )
        return spec

    def capability_for(self, analysis_kind: str) -> AnalysisCapability:
        capability = self.capabilities.get(analysis_kind)
        if capability is None:
            raise AnalysisError(
                AnalysisErrorCode.REQUEST_UNSUPPORTED,
                f"analysis kind {analysis_kind!r} is unsupported",
            )
        return capability

    def producer_for(self, analysis_kind: str, output: str) -> OutputProducer:
        producer = self.producers.get(output)
        if producer is None or analysis_kind not in producer.analysis_kinds:
            raise AnalysisError(
                AnalysisErrorCode.REQUEST_UNSUPPORTED,
                f"output {output!r} has no producer for {analysis_kind}",
            )
        return producer

    def validate(
        self,
        operation_specs: Mapping[str, Any] | frozenset[str] | None = None,
    ) -> None:
        errors: list[str] = []
        operation_names = (
            frozenset(operation_specs)
            if operation_specs is not None
            else None
        )
        specifications = (
            operation_specs if isinstance(operation_specs, Mapping) else {}
        )
        for name, capability in self.capabilities.items():
            if name != capability.analysis_kind:
                errors.append(f"capability key {name} disagrees with its kind")
            missing_fields = capability.semantic_fields.difference(self.fields)
            if missing_fields:
                errors.append(
                    f"{name} names unknown fields: {', '.join(sorted(missing_fields))}"
                )
            if set(capability.required_fields).difference(capability.semantic_fields):
                errors.append(f"{name} has required fields outside its field set")
            if set(capability.optional_fields).difference(capability.semantic_fields):
                errors.append(f"{name} has optional fields outside its field set")
            if set(capability.defaults).difference(capability.semantic_fields):
                errors.append(f"{name} has defaults outside its field set")
            duplicate_defaults = set(capability.defaults).intersection(
                capability.required_fields
            )
            if duplicate_defaults:
                errors.append(
                    f"{name} both requires and defaults: "
                    + ", ".join(sorted(duplicate_defaults))
                )
            for field_name, value in capability.defaults.items():
                try:
                    self.fields[field_name].validate(value)
                except AnalysisError:
                    errors.append(f"{name} has an invalid default for {field_name}")
            for output in capability.supported_outputs:
                producer = self.producers.get(output)
                if producer is None or name not in producer.analysis_kinds:
                    errors.append(f"{name}/{output} has no output producer")
            if capability.execution_mode == CapabilityExecutionMode.EXPLORATORY:
                if capability.exploratory_profile is None:
                    errors.append(f"{name} has no exploratory profile")
            elif capability.exploratory_profile is not None:
                errors.append(f"{name} unexpectedly has an exploratory profile")
        for output, producer in self.producers.items():
            if output != producer.output:
                errors.append(f"producer key {output} disagrees with its output")
            for kind in producer.analysis_kinds:
                producer_capability = self.capabilities.get(kind)
                if (
                    producer_capability is None
                    or output not in producer_capability.supported_outputs
                ):
                    errors.append(f"producer {producer.producer_id} is orphaned for {kind}")
            if operation_names is not None and producer.operation not in operation_names:
                errors.append(
                    f"producer {producer.producer_id} names missing operation {producer.operation}"
                )
            unknown_requirements = set(producer.required_fields).difference(
                self.fields,
            ).difference(_BOUND_DERIVED_FIELDS)
            if unknown_requirements:
                errors.append(
                    f"producer {producer.producer_id} requires unknown bound fields: "
                    + ", ".join(sorted(unknown_requirements))
                )
            specification = specifications.get(producer.operation)
            if (
                specification is not None
                and specification.result_type != producer.result_type
            ):
                errors.append(
                    f"producer {producer.producer_id} expects {producer.result_type} "
                    f"but {producer.operation} returns {specification.result_type}"
                )
            if specification is not None:
                input_schema = specification.input_schema
                properties = set(input_schema.get("properties", {}))
                required_arguments = set(input_schema.get("required", ()))
                unknown_arguments = set(producer.operation_arguments).difference(
                    properties
                )
                missing_arguments = required_arguments.difference(
                    producer.operation_arguments
                )
                if unknown_arguments:
                    errors.append(
                        f"producer {producer.producer_id} supplies unknown arguments: "
                        + ", ".join(sorted(unknown_arguments))
                    )
                if missing_arguments:
                    errors.append(
                        f"producer {producer.producer_id} cannot supply required "
                        "arguments: " + ", ".join(sorted(missing_arguments))
                    )
                if (
                    producer.prerequisite is not None
                    and producer.prerequisite
                    not in specification.permitted_dependency_types
                ):
                    errors.append(
                        f"producer {producer.producer_id} has incompatible "
                        f"prerequisite {producer.prerequisite}"
                    )
        if operation_names is not None:
            for capability in self.capabilities.values():
                profile = capability.exploratory_profile
                if profile:
                    missing = set(profile.operations).difference(operation_names)
                    if missing:
                        errors.append(
                            f"{capability.analysis_kind} profile names missing operations: "
                            + ", ".join(sorted(missing))
                        )
                    for operation in profile.operations:
                        specification = specifications.get(operation)
                        if specification is None:
                            continue
                        declared_types = set(
                            profile.permitted_dependency_types.get(operation, ())
                        )
                        unsupported_types = declared_types.difference(
                            specification.permitted_dependency_types
                        )
                        if unsupported_types:
                            errors.append(
                                f"{capability.analysis_kind}/{operation} permits "
                                "incompatible dependency types: "
                                + ", ".join(sorted(unsupported_types))
                            )
        if errors:
            raise AnalysisError(
                AnalysisErrorCode.CAPABILITY_INVALID,
                "; ".join(errors),
            )


def _adapter(annotation: Any) -> TypeAdapter[Any]:
    return TypeAdapter(annotation)


_ALL_KINDS = frozenset(ANALYSIS_KINDS)
_NUMERICAL_KINDS = frozenset(
    {"parameter_lookup", "reform_validation", "household", "society", "exploratory"}
)
_REFORM_KINDS = frozenset(
    {"reform_validation", "household", "society", "exploratory"}
)
_SOCIETY_KINDS = frozenset({"society", "exploratory"})
_BOUND_DERIVED_FIELDS = frozenset(
    {
        "parameter_path",
        "parameter_label",
        "aggregate_variable",
        "aggregate_variable_label",
        "reform_bindings",
        "reform_summary",
    }
)

_FIELDS: dict[str, SemanticFieldSpec] = {
    "analysis_kind": SemanticFieldSpec(
        "analysis_kind",
        _adapter(Literal[*ANALYSIS_KINDS]),
        _ALL_KINDS,
        EvidencePolicy.CONTROLLED,
        allow_clear=False,
        clarification_contract="analysis_kind",
        controlled_values=MappingProxyType(
            {
                "explain": "explanation",
                "explanation": "explanation",
                "parameter": "parameter_lookup",
                "parameter lookup": "parameter_lookup",
                "validate reform": "reform_validation",
                "household": "household",
                "benefit entitlement": "household",
                "society": "society",
                "population": "society",
                "simulation": "society",
                "exploratory": "exploratory",
                "explore": "exploratory",
            }
        ),
    ),
    "jurisdiction": SemanticFieldSpec(
        "jurisdiction",
        _adapter(StrictStr),
        _ALL_KINDS,
        EvidencePolicy.CONTROLLED,
        controlled_values=MappingProxyType(
            {"uk": "uk", "united kingdom": "uk", "gb": "uk", "britain": "uk"}
        ),
    ),
    "year": SemanticFieldSpec(
        "year",
        _adapter(Annotated[StrictInt, Field(ge=2000, le=2100)]),
        _NUMERICAL_KINDS,
        EvidencePolicy.EXACT,
        clarification_contract="simulation_year",
    ),
    "people": SemanticFieldSpec(
        "people",
        _adapter(list[dict[str, Any]]),
        frozenset({"household"}),
        EvidencePolicy.STRUCTURED,
        clarification_contract="household_people",
    ),
    "benunit": SemanticFieldSpec(
        "benunit",
        _adapter(dict[str, Any]),
        frozenset({"household"}),
        EvidencePolicy.STRUCTURED,
    ),
    "household": SemanticFieldSpec(
        "household",
        _adapter(dict[str, Any]),
        frozenset({"household"}),
        EvidencePolicy.STRUCTURED,
    ),
    "comparison_basis": SemanticFieldSpec(
        "comparison_basis",
        _adapter(Literal["income", "wealth"]),
        _SOCIETY_KINDS,
        EvidencePolicy.CONTROLLED,
        controlled_values=MappingProxyType(
            {"income": "income", "earnings": "income", "wealth": "wealth"}
        ),
    ),
    "parameter_query": SemanticFieldSpec(
        "parameter_query",
        _adapter(StrictStr),
        frozenset({"parameter_lookup"}),
        EvidencePolicy.NARRATIVE,
        clarification_contract="parameter_query",
    ),
    "variable_query": SemanticFieldSpec(
        "variable_query",
        _adapter(StrictStr),
        _SOCIETY_KINDS,
        EvidencePolicy.NARRATIVE,
        clarification_contract="variable_query",
    ),
    "reform_intent": SemanticFieldSpec(
        "reform_intent",
        _adapter(StrictStr),
        _REFORM_KINDS,
        EvidencePolicy.NARRATIVE,
        clarification_contract="reform_target",
    ),
    "reform_instruction": SemanticFieldSpec(
        "reform_instruction",
        REFORM_INSTRUCTION_ADAPTER,
        _REFORM_KINDS,
        EvidencePolicy.EXACT,
        clarification_contract="reform_instruction",
    ),
    "reform": SemanticFieldSpec(
        "reform",
        _adapter(dict[str, Any]),
        _REFORM_KINDS,
        EvidencePolicy.STRUCTURED,
        clarification_contract="reform",
    ),
    "aggregate_entity": SemanticFieldSpec(
        "aggregate_entity",
        _adapter(Literal["person", "benunit", "household"]),
        _SOCIETY_KINDS,
        EvidencePolicy.CONTROLLED,
        clarification_contract="aggregate_entity",
    ),
    "aggregate_operation": SemanticFieldSpec(
        "aggregate_operation",
        _adapter(Literal["sum", "mean", "count"]),
        _SOCIETY_KINDS,
        EvidencePolicy.CONTROLLED,
        clarification_contract="aggregate_operation",
    ),
    "aggregate_target": SemanticFieldSpec(
        "aggregate_target",
        _adapter(Literal["baseline", "reform", "change"]),
        _SOCIETY_KINDS,
        EvidencePolicy.CONTROLLED,
    ),
    "filter_variable": SemanticFieldSpec(
        "filter_variable", _adapter(StrictStr), _SOCIETY_KINDS, EvidencePolicy.NARRATIVE
    ),
    "filter_variable_eq": SemanticFieldSpec(
        "filter_variable_eq",
        _adapter(StrictStr | StrictInt | StrictFloat | StrictBool),
        _SOCIETY_KINDS,
        EvidencePolicy.EXACT,
    ),
    "filter_variable_leq": SemanticFieldSpec(
        "filter_variable_leq",
        _adapter(StrictInt | StrictFloat),
        _SOCIETY_KINDS,
        EvidencePolicy.EXACT,
    ),
    "filter_variable_geq": SemanticFieldSpec(
        "filter_variable_geq",
        _adapter(StrictInt | StrictFloat),
        _SOCIETY_KINDS,
        EvidencePolicy.EXACT,
    ),
    "chart_kind": SemanticFieldSpec(
        "chart_kind",
        _adapter(
            Literal[
                "budget_waterfall",
                "program_budget_waterfall",
                "decile_absolute_bar",
                "decile_relative_bar",
                "winners_losers_stacked_bar",
                "poverty_relative_bar",
                "inequality_relative_bar",
            ]
        ),
        _SOCIETY_KINDS,
        EvidencePolicy.CONTROLLED,
        controlled_values=MappingProxyType(
            {
                "budget waterfall": "budget_waterfall",
                "programme budget waterfall": "program_budget_waterfall",
                "program budget waterfall": "program_budget_waterfall",
                "decile absolute": "decile_absolute_bar",
                "decile relative": "decile_relative_bar",
                "winners and losers": "winners_losers_stacked_bar",
                "poverty relative": "poverty_relative_bar",
                "inequality relative": "inequality_relative_bar",
            }
        ),
    ),
    "chart_title": SemanticFieldSpec(
        "chart_title", _adapter(StrictStr), _SOCIETY_KINDS, EvidencePolicy.NARRATIVE
    ),
    "decile_concept": SemanticFieldSpec(
        "decile_concept",
        _adapter(
            Literal[
                "household_net_income",
                "equivalised_hbai_net_income",
                "wealth",
            ]
        ),
        _SOCIETY_KINDS,
        EvidencePolicy.CONTROLLED,
    ),
    "programs": SemanticFieldSpec(
        "programs",
        _adapter(tuple[StrictStr, ...]),
        _SOCIETY_KINDS,
        EvidencePolicy.STRUCTURED,
    ),
    "objective": SemanticFieldSpec(
        "objective",
        _adapter(StrictStr),
        frozenset({"exploratory"}),
        EvidencePolicy.NARRATIVE,
        clarification_contract="exploratory_objective",
    ),
    "assumptions": SemanticFieldSpec(
        "assumptions",
        _adapter(tuple[StrictStr, ...]),
        _ALL_KINDS,
        EvidencePolicy.STRUCTURED,
    ),
}


_SOCIETY_OUTPUTS = frozenset(
    {
        "budgetary_impact",
        "tax_revenue",
        "benefit_spending",
        "poverty_impact",
        "inequality_impact",
        "decile_impact",
        "winners_losers",
        "caseload",
        "marginal_rate",
        "aggregate",
        "program_breakdown",
        "chart",
    }
)

_COMMON_FIELDS = frozenset({"analysis_kind", "jurisdiction", "assumptions"})
_REFORM_FIELDS = frozenset({"reform_intent", "reform_instruction", "reform"})
_SOCIETY_FIELDS = frozenset(
    {
        "year",
        "comparison_basis",
        "variable_query",
        "aggregate_entity",
        "aggregate_operation",
        "aggregate_target",
        "filter_variable",
        "filter_variable_eq",
        "filter_variable_leq",
        "filter_variable_geq",
        "chart_kind",
        "chart_title",
        "decile_concept",
        "programs",
    }
)

_EXPLORATORY_PROFILE = ExploratoryProfile(
    operations=(
        "compute_budgetary_impact",
        "compute_program_breakdown",
        "compute_decile_impacts",
        "compute_winners_losers",
        "compute_poverty_metrics",
        "compute_inequality_metrics",
        "aggregate_result",
        "generate_chart",
    ),
    permitted_dependencies=MappingProxyType(
        {
            "compute_budgetary_impact": ("society_simulation",),
            "compute_program_breakdown": ("society_simulation",),
            "compute_decile_impacts": ("society_simulation",),
            "compute_winners_losers": ("society_simulation",),
            "compute_poverty_metrics": ("society_simulation",),
            "compute_inequality_metrics": ("society_simulation",),
            "aggregate_result": ("society_simulation",),
            "generate_chart": (
                "budgetary_impact",
                "program_breakdown",
                "decile_impacts",
                "winners_losers",
                "poverty_metrics",
                "inequality_metrics",
                "aggregate_result",
            ),
        }
    ),
    permitted_dependency_types=MappingProxyType(
        {
            "compute_budgetary_impact": ("society_simulation",),
            "compute_program_breakdown": ("society_simulation",),
            "compute_decile_impacts": ("society_simulation",),
            "compute_winners_losers": ("society_simulation",),
            "compute_poverty_metrics": ("society_simulation",),
            "compute_inequality_metrics": ("society_simulation",),
            "aggregate_result": ("society_simulation",),
            "generate_chart": (
                "budgetary_impact",
                "program_breakdown",
                "decile_impacts",
                "winners_losers",
                "poverty_metrics",
                "inequality_metrics",
                "aggregate_result",
            ),
        }
    ),
    max_model_iterations=4,
    max_operation_calls=6,
)

_CAPABILITIES: dict[str, AnalysisCapability] = {
    "explanation": AnalysisCapability(
        analysis_kind="explanation",
        semantic_fields=_COMMON_FIELDS,
        required_fields=("analysis_kind",),
        optional_fields=("jurisdiction", "assumptions"),
        defaults=MappingProxyType({"jurisdiction": "uk"}),
        supported_outputs=frozenset(),
        default_outputs=(),
        execution_mode=CapabilityExecutionMode.EXPLANATION,
        standard_template="explanation",
    ),
    "parameter_lookup": AnalysisCapability(
        analysis_kind="parameter_lookup",
        semantic_fields=_COMMON_FIELDS | {"year", "parameter_query"},
        required_fields=("analysis_kind", "parameter_query"),
        optional_fields=("jurisdiction", "year", "assumptions"),
        defaults=MappingProxyType({"jurisdiction": "uk"}),
        supported_outputs=frozenset({"parameter_lookup"}),
        default_outputs=("parameter_lookup",),
        execution_mode=CapabilityExecutionMode.STANDARD,
        standard_template="parameter_lookup",
    ),
    "reform_validation": AnalysisCapability(
        analysis_kind="reform_validation",
        semantic_fields=_COMMON_FIELDS | {"year"} | _REFORM_FIELDS,
        required_fields=("analysis_kind",),
        optional_fields=(
            "jurisdiction",
            "year",
            "reform_intent",
            "reform_instruction",
            "reform",
            "assumptions",
        ),
        defaults=MappingProxyType({"jurisdiction": "uk"}),
        supported_outputs=frozenset({"reform_validity"}),
        default_outputs=("reform_validity",),
        execution_mode=CapabilityExecutionMode.STANDARD,
        standard_template="reform_validation",
    ),
    "household": AnalysisCapability(
        analysis_kind="household",
        semantic_fields=_COMMON_FIELDS
        | {"year", "people", "benunit", "household"}
        | _REFORM_FIELDS,
        required_fields=("analysis_kind", "people"),
        optional_fields=(
            "jurisdiction",
            "year",
            "benunit",
            "household",
            "reform_intent",
            "reform_instruction",
            "reform",
            "assumptions",
        ),
        defaults=MappingProxyType({"jurisdiction": "uk"}),
        supported_outputs=frozenset({"net_income", "benefit_entitlement"}),
        default_outputs=("net_income",),
        execution_mode=CapabilityExecutionMode.STANDARD,
        standard_template="household",
    ),
    "society": AnalysisCapability(
        analysis_kind="society",
        semantic_fields=_COMMON_FIELDS | _SOCIETY_FIELDS | _REFORM_FIELDS,
        required_fields=("analysis_kind",),
        optional_fields=tuple(
            sorted((_COMMON_FIELDS | _SOCIETY_FIELDS | _REFORM_FIELDS) - {"analysis_kind"})
        ),
        defaults=MappingProxyType({"jurisdiction": "uk", "aggregate_target": "reform"}),
        supported_outputs=_SOCIETY_OUTPUTS,
        default_outputs=(),
        execution_mode=CapabilityExecutionMode.STANDARD,
        standard_template="society",
    ),
    "exploratory": AnalysisCapability(
        analysis_kind="exploratory",
        semantic_fields=_COMMON_FIELDS
        | _SOCIETY_FIELDS
        | _REFORM_FIELDS
        | {"objective"},
        required_fields=("analysis_kind", "objective"),
        optional_fields=tuple(
            sorted(
                (_COMMON_FIELDS | _SOCIETY_FIELDS | _REFORM_FIELDS | {"objective"})
                - {"analysis_kind", "objective"}
            )
        ),
        defaults=MappingProxyType({"jurisdiction": "uk", "aggregate_target": "reform"}),
        supported_outputs=_SOCIETY_OUTPUTS,
        default_outputs=(),
        execution_mode=CapabilityExecutionMode.EXPLORATORY,
        exploratory_profile=_EXPLORATORY_PROFILE,
    ),
}


def _producer(
    output: str,
    operation: str,
    result_type: str,
    *,
    kinds: frozenset[str],
    required_fields: tuple[str, ...] = (),
    prerequisite: str | None = None,
    source_output: str | None = None,
) -> OutputProducer:
    operation_arguments = {
        "get_parameter": frozenset({"path", "year"}),
        "validate_reform": frozenset({"reform", "year"}),
        "run_household_simulation": frozenset(
            {"people", "benunit", "household", "year", "reform"}
        ),
        "compute_budgetary_impact": frozenset({"simulation_id"}),
        "compute_program_breakdown": frozenset({"simulation_id", "programs"}),
        "compute_decile_impacts": frozenset({"simulation_id", "decile_concept"}),
        "compute_winners_losers": frozenset({"simulation_id", "basis"}),
        "compute_poverty_metrics": frozenset({"simulation_id"}),
        "compute_inequality_metrics": frozenset({"simulation_id"}),
        "aggregate_result": frozenset(
            {
                "simulation_id",
                "target",
                "entity",
                "variable",
                "operation",
                "filter_variable",
                "filter_variable_eq",
                "filter_variable_leq",
                "filter_variable_geq",
            }
        ),
        "generate_chart": frozenset({"chart_kind", "result_id", "title"}),
    }[operation]
    return OutputProducer(
        producer_id=f"producer:{output}",
        output=output,
        analysis_kinds=kinds,
        result_type=result_type,
        operation=operation,
        operation_arguments=operation_arguments,
        required_fields=required_fields,
        prerequisite=prerequisite,
        source_output=source_output,
    )


_PRODUCERS = {
    "parameter_lookup": _producer(
        "parameter_lookup", "get_parameter", "parameter", kinds=frozenset({"parameter_lookup"}), required_fields=("parameter_path",)
    ),
    "reform_validity": _producer(
        "reform_validity", "validate_reform", "reform_validation", kinds=frozenset({"reform_validation"}), required_fields=("reform",)
    ),
    "net_income": _producer(
        "net_income", "run_household_simulation", "household_simulation", kinds=frozenset({"household"}), required_fields=("people",)
    ),
    "benefit_entitlement": _producer(
        "benefit_entitlement", "run_household_simulation", "household_simulation", kinds=frozenset({"household"}), required_fields=("people",)
    ),
    "budgetary_impact": _producer(
        "budgetary_impact", "compute_budgetary_impact", "budgetary_impact", kinds=_SOCIETY_KINDS, prerequisite="society_simulation"
    ),
    "tax_revenue": _producer(
        "tax_revenue", "compute_budgetary_impact", "budgetary_impact", kinds=_SOCIETY_KINDS, prerequisite="society_simulation"
    ),
    "benefit_spending": _producer(
        "benefit_spending", "compute_budgetary_impact", "budgetary_impact", kinds=_SOCIETY_KINDS, prerequisite="society_simulation"
    ),
    "poverty_impact": _producer(
        "poverty_impact", "compute_poverty_metrics", "poverty_metrics", kinds=_SOCIETY_KINDS, prerequisite="society_simulation"
    ),
    "inequality_impact": _producer(
        "inequality_impact", "compute_inequality_metrics", "inequality_metrics", kinds=_SOCIETY_KINDS, prerequisite="society_simulation"
    ),
    "decile_impact": _producer(
        "decile_impact", "compute_decile_impacts", "decile_impacts", kinds=_SOCIETY_KINDS, prerequisite="society_simulation"
    ),
    "winners_losers": _producer(
        "winners_losers", "compute_winners_losers", "winners_losers", kinds=_SOCIETY_KINDS, prerequisite="society_simulation"
    ),
    "program_breakdown": _producer(
        "program_breakdown", "compute_program_breakdown", "program_breakdown", kinds=_SOCIETY_KINDS, prerequisite="society_simulation"
    ),
    "aggregate": _producer(
        "aggregate", "aggregate_result", "aggregate_result", kinds=_SOCIETY_KINDS, required_fields=("aggregate_variable", "aggregate_entity", "aggregate_operation"), prerequisite="society_simulation"
    ),
    "caseload": _producer(
        "caseload", "aggregate_result", "aggregate_result", kinds=_SOCIETY_KINDS, required_fields=("aggregate_variable", "aggregate_entity"), prerequisite="society_simulation"
    ),
    "marginal_rate": _producer(
        "marginal_rate", "aggregate_result", "aggregate_result", kinds=_SOCIETY_KINDS, required_fields=("aggregate_variable", "aggregate_entity"), prerequisite="society_simulation"
    ),
    "chart": _producer(
        "chart", "generate_chart", "chart", kinds=_SOCIETY_KINDS, required_fields=("chart_kind",), source_output="chart_source"
    ),
}


CAPABILITY_REGISTRY = CapabilityRegistry(
    version=CAPABILITY_VERSION,
    fields=MappingProxyType(_FIELDS),
    capabilities=MappingProxyType(_CAPABILITIES),
    producers=MappingProxyType(_PRODUCERS),
)
CAPABILITY_REGISTRY.validate()


def supported_outputs() -> frozenset[str]:
    return frozenset(CAPABILITY_REGISTRY.producers)


def semantic_revision_field_names(
    registry: CapabilityRegistry = CAPABILITY_REGISTRY,
) -> frozenset[str]:
    """Fields that may exist in semantic history, including legacy values."""

    return frozenset(registry.fields).difference({"analysis_kind"})


def semantic_candidate_field_names(
    registry: CapabilityRegistry = CAPABILITY_REGISTRY,
) -> frozenset[str]:
    # `reform` is the normalized parameter-to-value object produced by the
    # binder.  It remains a registered field so legacy semantic revisions can
    # be read and bound, but it is never legal model-authored input.  Models
    # express policy changes through `reform_intent` and
    # `reform_instruction`; deterministic binding is the only active path that
    # creates `reform`.
    return semantic_revision_field_names(registry).difference({"reform"})


def validate_capabilities_against_operations() -> None:
    from analysis.operations import default_operation_catalogue

    default_operation_catalogue().validate(CAPABILITY_REGISTRY)
