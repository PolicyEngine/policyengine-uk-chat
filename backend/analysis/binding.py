"""Authoritative binding from semantic revisions to compiler-ready requests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from analysis.capabilities import (
    CAPABILITY_REGISTRY,
    CapabilityRegistry,
)
from analysis.catalogue import (
    CatalogueCandidate,
    CatalogueResolution,
    require_catalogue,
    resolve_catalogue_term,
)
from analysis.clarifications import create_clarification
from analysis.common import (
    AnalysisError,
    AnalysisErrorCode,
    RuntimeVersions,
    canonical_hash,
    stable_identifier,
)
from analysis.models import (
    AbolishReform,
    ApplyNamedReformTransformation,
    BoundRequest,
    ChangeReformByAmount,
    ChangeReformByPercent,
    DirectionOnlyReform,
    FieldProvenance,
    ModelUsageEntry,
    PendingClarification,
    RequestField,
    SemanticRequestRevision,
    SetExactReform,
    SetReformToggle,
)
from analysis.operations import OperationCatalogue, default_operation_catalogue


@dataclass(frozen=True)
class ReformTargetSelectionRequest:
    intent: str
    candidates: tuple[CatalogueCandidate, ...]
    year: int
    session_id: str
    turn_id: str


@dataclass(frozen=True)
class ReformTargetSelection:
    bindings: tuple[CatalogueCandidate, ...]
    usage_entry: ModelUsageEntry | None = None
    error: str | None = None


@dataclass(frozen=True)
class Ready:
    bound_request: BoundRequest
    usage_entries: tuple[ModelUsageEntry, ...] = ()


@dataclass(frozen=True)
class NeedsClarification:
    clarification: PendingClarification
    usage_entries: tuple[ModelUsageEntry, ...] = ()


@dataclass(frozen=True)
class Unsupported:
    reason: str
    usage_entries: tuple[ModelUsageEntry, ...] = ()


@dataclass(frozen=True)
class BindingFailed:
    reason: str
    error_code: AnalysisErrorCode = AnalysisErrorCode.BINDING_FAILED
    usage_entries: tuple[ModelUsageEntry, ...] = ()


BindingDecision = Ready | NeedsClarification | Unsupported | BindingFailed
CatalogueResolver = Callable[[str, str], CatalogueResolution]
ReformTargetSelector = Callable[
    [ReformTargetSelectionRequest],
    ReformTargetSelection | None,
]
ReformValidator = Callable[[dict[str, Any], int], dict[str, Any]]
CurrentValueResolver = Callable[[tuple[str, ...], int], dict[str, Any]]
InactiveValueResolver = Callable[[tuple[str, ...], int], dict[str, Any]]
HouseholdValidator = Callable[..., dict[str, Any]]


def _default_year() -> int:
    from tools.definitions import DEFAULT_SIMULATION_YEAR

    return DEFAULT_SIMULATION_YEAR


def _validate_reform(reform: dict[str, Any], year: int) -> dict[str, Any]:
    from engine.reforms import validate_reform_dict

    return validate_reform_dict(reform, year=year)


def _validate_household(**arguments: Any) -> dict[str, Any]:
    from engine.households import validate_household_dict

    return validate_household_dict(**arguments)


def _parameter_metadata(paths: tuple[str, ...], year: int) -> dict[str, dict[str, Any]]:
    from engine.discovery import get_parameter

    metadata: dict[str, dict[str, Any]] = {}
    for path in paths:
        result = get_parameter(path=path, year=year)
        parameter = result.get("parameter") if isinstance(result, dict) else None
        if (
            not isinstance(result, dict)
            or result.get("status") != "success"
            or not isinstance(parameter, dict)
        ):
            raise AnalysisError(
                AnalysisErrorCode.BINDING_FAILED,
                f"authoritative metadata is unavailable for reform parameter {path}",
            )
        metadata[path] = parameter
    return metadata


def _current_parameter_values(
    paths: tuple[str, ...],
    year: int,
) -> dict[str, Any]:
    return {
        path: metadata.get("value")
        for path, metadata in _parameter_metadata(paths, year).items()
    }


def _inactive_parameter_values(
    paths: tuple[str, ...],
    year: int,
) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for path, metadata in _parameter_metadata(paths, year).items():
        if "inactive_value" not in metadata or metadata["inactive_value"] is None:
            raise AnalysisError(
                AnalysisErrorCode.BINDING_FAILED,
                f"inactive metadata is unavailable for reform parameter {path}",
            )
        values[path] = metadata["inactive_value"]
    return values


def _resolve_catalogue(kind: str, query: str) -> CatalogueResolution:
    return resolve_catalogue_term(kind, query)


@dataclass(frozen=True)
class BindingServices:
    """External value resolvers used by authoritative request binding."""

    catalogue_resolver: CatalogueResolver = _resolve_catalogue
    reform_target_selector: ReformTargetSelector | None = None
    reform_validator: ReformValidator = _validate_reform
    current_value_resolver: CurrentValueResolver = _current_parameter_values
    inactive_value_resolver: InactiveValueResolver = _inactive_parameter_values
    household_validator: HouseholdValidator = _validate_household
    default_year: int | None = None


def _field_value(fields: dict[str, RequestField], name: str, default=None):
    field = fields.get(name)
    return field.value if field is not None else default


def _set_default_if_absent(
    fields: dict[str, RequestField],
    name: str,
    value: Any,
) -> None:
    if name not in fields:
        fields[name] = RequestField(value=value, provenance=FieldProvenance.DEFAULT)


def _catalogue_resolver(resolver: CatalogueResolver | None) -> CatalogueResolver:
    return resolver or (lambda kind, query: resolve_catalogue_term(kind, query))


def _clarify(
    revision: SemanticRequestRevision,
    target_field: str,
    reason_code: str,
    *,
    prompt: str | None = None,
    choices: tuple[str, ...] = (),
    usage_entries: tuple[ModelUsageEntry, ...] = (),
) -> NeedsClarification:
    return NeedsClarification(
        clarification=create_clarification(
            revision=revision,
            target_field=target_field,
            reason_code=reason_code,
            prompt=prompt,
            choices=choices,
        ),
        usage_entries=usage_entries,
    )


def _resolve_single_catalogue_candidate(
    *,
    revision: SemanticRequestRevision,
    target_field: str,
    kind: str,
    query: str,
    resolver: CatalogueResolver,
    prompt: str,
) -> CatalogueCandidate | NeedsClarification:
    resolution = require_catalogue(resolver(kind, query))
    matches = resolution.authoritative
    if len(matches) != 1:
        return _clarify(
            revision,
            target_field,
            "ambiguous_catalogue_match",
            prompt=prompt,
            choices=tuple(
                item.label for item in (matches or resolution.candidates[:3])
            ),
        )
    return matches[0]


def _apply_reform_instruction(
    instruction: Any,
    *,
    paths: tuple[str, ...],
    year: int,
    current_value_resolver: CurrentValueResolver,
    inactive_value_resolver: InactiveValueResolver,
) -> dict[str, Any] | None:
    if isinstance(instruction, DirectionOnlyReform):
        return None
    if isinstance(instruction, SetExactReform):
        return {path: instruction.value for path in paths}
    if isinstance(instruction, SetReformToggle):
        return {path: instruction.value for path in paths}
    if isinstance(instruction, AbolishReform):
        inactive = inactive_value_resolver(paths, year)
        return {path: inactive[path] for path in paths}
    if isinstance(instruction, (ChangeReformByAmount, ChangeReformByPercent)):
        current = current_value_resolver(paths, year)
        values: dict[str, Any] = {}
        for path in paths:
            value = current.get(path)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise AnalysisError(
                    AnalysisErrorCode.BINDING_FAILED,
                    f"numeric transformation is incompatible with {path}",
                )
            if isinstance(instruction, ChangeReformByAmount):
                values[path] = value + instruction.amount
            else:
                values[path] = value * (1 + instruction.percent / 100)
        return values
    if isinstance(instruction, ApplyNamedReformTransformation):
        current = current_value_resolver(paths, year)
        if instruction.identifier not in {"double", "halve"}:
            raise AnalysisError(
                AnalysisErrorCode.REQUEST_UNSUPPORTED,
                f"named reform transformation {instruction.identifier!r} is unsupported",
            )
        multiplier = 2 if instruction.identifier == "double" else 0.5
        values = {}
        for path in paths:
            value = current.get(path)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise AnalysisError(
                    AnalysisErrorCode.BINDING_FAILED,
                    f"named numeric transformation is incompatible with {path}",
                )
            values[path] = value * multiplier
        return values
    raise AnalysisError(
        AnalysisErrorCode.BINDING_FAILED,
        "the reform instruction has no deterministic implementation",
    )


def _bind_reform(
    revision: SemanticRequestRevision,
    fields: dict[str, RequestField],
    *,
    year: int,
    resolver: CatalogueResolver,
    target_selector: ReformTargetSelector | None,
    reform_validator: ReformValidator,
    current_value_resolver: CurrentValueResolver,
    inactive_value_resolver: InactiveValueResolver,
) -> tuple[dict[str, RequestField], tuple[ModelUsageEntry, ...]] | BindingDecision:
    direct_reform = _field_value(fields, "reform")
    if direct_reform is not None:
        if not isinstance(direct_reform, dict):
            return Unsupported("reform must be a parameter-to-value object")
        validation = reform_validator(direct_reform, year)
        if not validation.get("valid"):
            return _clarify(
                revision,
                "reform",
                "confirm_reform",
                prompt="The policy change is not valid for this year. What should I change?",
            )
        bound_fields = dict(fields)
        bound_fields["reform"] = RequestField(
            value=validation.get("normalized_reform", direct_reform),
            provenance=FieldProvenance.CATALOGUE,
            evidence=fields["reform"].evidence,
        )
        return bound_fields, ()

    intent = _field_value(fields, "reform_intent")
    instruction = _field_value(fields, "reform_instruction")
    if intent is None and instruction is None:
        return fields, ()
    if not isinstance(intent, str) or not intent.strip():
        return _clarify(
            revision,
            "reform_intent",
            "unsupported_partial_request",
            prompt="Which current UK policy parameter should be changed?",
        )
    if instruction is None:
        return _clarify(
            revision,
            "reform_instruction",
            "confirm_reform",
            prompt="What exact change should I apply to that policy parameter?",
        )
    if isinstance(instruction, DirectionOnlyReform):
        return _clarify(
            revision,
            "reform_instruction",
            "confirm_reform",
            prompt=(
                "What exact value, amount, percentage, abolition, or registered "
                "transformation should I apply?"
            ),
        )

    resolution = require_catalogue(resolver("reform_target", intent.strip()))
    candidates = resolution.authoritative
    if not candidates:
        return _clarify(
            revision,
            "reform_intent",
            "unsupported_partial_request",
            prompt=(
                "I could not match that policy change to a current UK parameter. "
                "Could you name it more precisely?"
            ),
            choices=tuple(item.label for item in resolution.candidates[:3]),
        )
    usage_entries: tuple[ModelUsageEntry, ...] = ()
    if len(candidates) == 1:
        bindings = (candidates[0],)
    elif target_selector is None:
        return _clarify(
            revision,
            "reform_intent",
            "ambiguous_catalogue_match",
            choices=tuple(item.label for item in candidates),
        )
    else:
        selection = target_selector(
            ReformTargetSelectionRequest(
                intent=intent,
                candidates=candidates,
                year=year,
                session_id=revision.session_id,
                turn_id=revision.turn_id,
            )
        )
        selection_usage = (
            (selection.usage_entry,)
            if selection is not None and selection.usage_entry is not None
            else ()
        )
        if selection is not None and selection.error:
            return BindingFailed(selection.error, usage_entries=selection_usage)
        if selection is None or not selection.bindings:
            return _clarify(
                revision,
                "reform_intent",
                "ambiguous_catalogue_match",
                choices=tuple(item.label for item in candidates),
                usage_entries=selection_usage,
            )
        candidate_ids = {candidate.identifier for candidate in candidates}
        if any(binding.identifier not in candidate_ids for binding in selection.bindings):
            return BindingFailed(
                "reform target selection referenced a target outside the authoritative candidates"
            )
        bindings = selection.bindings
        usage_entries = selection_usage

    paths = tuple(dict.fromkeys(binding.identifier for binding in bindings))
    try:
        reform = _apply_reform_instruction(
            instruction,
            paths=paths,
            year=year,
            current_value_resolver=current_value_resolver,
            inactive_value_resolver=inactive_value_resolver,
        )
    except AnalysisError as exc:
        if exc.code == AnalysisErrorCode.REQUEST_UNSUPPORTED:
            return Unsupported(str(exc), usage_entries)
        return BindingFailed(str(exc), exc.code, usage_entries)
    if reform is None:
        return _clarify(
            revision,
            "reform_instruction",
            "confirm_reform",
            usage_entries=usage_entries,
        )
    validation = reform_validator(reform, year)
    if not validation.get("valid"):
        return BindingFailed(
            "deterministically constructed reform failed PolicyEngine validation",
            usage_entries=usage_entries,
        )
    bound_fields = dict(fields)
    bound_fields["reform"] = RequestField(
        value=validation.get("normalized_reform", reform),
        provenance=FieldProvenance.CATALOGUE,
        evidence=fields["reform_intent"].evidence,
    )
    bound_fields["reform_bindings"] = RequestField(
        value=[
            {"identifier": binding.identifier, "label": binding.label}
            for binding in bindings
        ],
        provenance=FieldProvenance.CATALOGUE,
        evidence=fields["reform_intent"].evidence,
    )
    bound_fields["reform_summary"] = RequestField(
        value=(
            f"Apply {instruction.kind.replace('_', ' ')} to "
            + ", ".join(binding.label for binding in bindings)
        ),
        provenance=FieldProvenance.CATALOGUE,
    )
    return bound_fields, usage_entries


class _RequestBinderImplementation:
    """Resolve server defaults and authoritative identifiers without mutation."""

    @staticmethod
    def bind(
        revision: SemanticRequestRevision,
        *,
        runtime_versions: RuntimeVersions,
        registry: CapabilityRegistry = CAPABILITY_REGISTRY,
        catalogue_resolver: CatalogueResolver | None = None,
        reform_target_selector: ReformTargetSelector | None = None,
        reform_validator: ReformValidator = _validate_reform,
        current_value_resolver: CurrentValueResolver = _current_parameter_values,
        inactive_value_resolver: InactiveValueResolver = _inactive_parameter_values,
        household_validator: HouseholdValidator = _validate_household,
        default_year: int | None = None,
        operation_catalogue=None,
    ) -> BindingDecision:
        if operation_catalogue is None:
            from analysis.operations import default_operation_catalogue

            operation_catalogue = default_operation_catalogue()
        fields = dict(revision.fields)
        kind = _field_value(fields, "analysis_kind")
        if not isinstance(kind, str):
            return _clarify(
                revision, "analysis_kind", "missing_analysis_kind"
            )
        try:
            capability = registry.capability_for(kind)
        except AnalysisError as exc:
            return Unsupported(str(exc))

        for name, value in capability.defaults.items():
            _set_default_if_absent(fields, name, value)
        if kind != "explanation":
            _set_default_if_absent(
                fields,
                "year",
                default_year if default_year is not None else _default_year(),
            )
        jurisdiction = _field_value(fields, "jurisdiction")
        if jurisdiction != "uk":
            return Unsupported("only UK tax and benefit analysis is supported")

        for required in capability.required_fields:
            if required not in fields:
                reason = {
                    "analysis_kind": "missing_analysis_kind",
                    "people": "missing_household",
                    "parameter_query": "missing_parameter",
                    "objective": "unsupported_partial_request",
                }.get(required, "unsupported_partial_request")
                return _clarify(revision, required, reason)

        resolver = _catalogue_resolver(catalogue_resolver)
        if kind == "parameter_lookup":
            query = _field_value(fields, "parameter_query")
            match = _resolve_single_catalogue_candidate(
                revision=revision,
                target_field="parameter_query",
                kind="reform_target",
                query=str(query),
                resolver=resolver,
                prompt="Which exact tax or benefit parameter did you mean?",
            )
            if isinstance(match, NeedsClarification):
                return match
            fields["parameter_path"] = RequestField(
                value=match.identifier,
                provenance=FieldProvenance.CATALOGUE,
                evidence=fields["parameter_query"].evidence,
            )
            fields["parameter_label"] = RequestField(
                value=match.label,
                provenance=FieldProvenance.CATALOGUE,
                evidence=fields["parameter_query"].evidence,
            )

        requested_outputs = revision.outputs or capability.default_outputs
        if kind in {"society", "exploratory"} and not requested_outputs:
            return _clarify(revision, "outputs", "missing_output")
        producer_outputs = list(requested_outputs)
        if "chart" in producer_outputs:
            chart_kind = _field_value(fields, "chart_kind")
            recipe = operation_catalogue.chart_recipes.get(chart_kind)
            if recipe is None:
                return _clarify(
                    revision,
                    "chart_kind",
                    "unsupported_partial_request",
                    prompt="Which supported chart format should I use?",
                )
            source_output = recipe.source_output
            if source_output not in producer_outputs:
                producer_outputs.insert(0, source_output)

        aggregate_outputs = set(producer_outputs).intersection(
            {"aggregate", "caseload", "marginal_rate"}
        )
        if aggregate_outputs:
            query = _field_value(fields, "variable_query")
            if not isinstance(query, str) or not query.strip():
                return _clarify(
                    revision, "variable_query", "missing_aggregate_variable"
                )
            match = _resolve_single_catalogue_candidate(
                revision=revision,
                target_field="variable_query",
                kind="variable",
                query=query,
                resolver=resolver,
                prompt="Which exact model variable should I aggregate?",
            )
            if isinstance(match, NeedsClarification):
                return match
            fields["aggregate_variable"] = RequestField(
                value=match.identifier,
                provenance=FieldProvenance.CATALOGUE,
                evidence=fields["variable_query"].evidence,
            )
            fields["aggregate_variable_label"] = RequestField(
                value=match.label,
                provenance=FieldProvenance.CATALOGUE,
                evidence=fields["variable_query"].evidence,
            )
            if "aggregate_entity" not in fields:
                return _clarify(
                    revision, "aggregate_entity", "missing_aggregate_entity"
                )
            if "aggregate" in aggregate_outputs and "aggregate_operation" not in fields:
                return _clarify(
                    revision, "aggregate_operation", "missing_aggregate_operation"
                )

        year = _field_value(fields, "year")
        reform_result = _bind_reform(
            revision,
            fields,
            year=year,
            resolver=resolver,
            target_selector=reform_target_selector,
            reform_validator=reform_validator,
            current_value_resolver=current_value_resolver,
            inactive_value_resolver=inactive_value_resolver,
        )
        if isinstance(reform_result, (NeedsClarification, Unsupported, BindingFailed)):
            return reform_result
        fields, usage_entries = reform_result

        if kind == "household":
            household_validation = household_validator(
                people=_field_value(fields, "people"),
                benunit=_field_value(fields, "benunit"),
                household=_field_value(fields, "household"),
                year=year,
                reform=_field_value(fields, "reform"),
            )
            if not household_validation.get("valid"):
                errors = household_validation.get("errors")
                detail = (
                    errors[0].get("message")
                    if isinstance(errors, list)
                    and errors
                    and isinstance(errors[0], dict)
                    else "the household description is invalid"
                )
                return _clarify(
                    revision,
                    "people",
                    "missing_household",
                    prompt=f"Please correct the household description: {detail}",
                    usage_entries=usage_entries,
                )

        if kind == "reform_validation" and "reform" not in fields:
            return _clarify(
                revision,
                "reform_intent",
                "unsupported_partial_request",
                prompt="Which exact policy change should I validate?",
                usage_entries=usage_entries,
            )

        producers = []
        for output in producer_outputs:
            try:
                producer = registry.producer_for(kind, output)
            except AnalysisError as exc:
                return Unsupported(str(exc), usage_entries)
            missing = [name for name in producer.required_fields if name not in fields]
            if missing:
                return BindingFailed(
                    f"producer {producer.producer_id} is missing inputs: "
                    + ", ".join(missing),
                    usage_entries=usage_entries,
                )
            producers.append(producer)

        bound_body = {
            "session_id": revision.session_id,
            "request_revision_id": revision.revision_id,
            "fields": {
                name: value.model_dump(mode="json") for name, value in fields.items()
            },
            "outputs": requested_outputs,
            "producer_outputs": producer_outputs,
            "output_producers": [producer.producer_id for producer in producers],
            "capability_version": registry.version,
            "catalogue_version": runtime_versions.catalogue_version,
            "engine_version": runtime_versions.engine_version,
            "country_package_version": runtime_versions.country_package_version,
            "dataset_identifier": runtime_versions.dataset_identifier,
            "plan_schema_version": runtime_versions.plan_schema_version,
        }
        bound_request = BoundRequest(
            bound_request_id=stable_identifier(
                "bound", revision.revision_id, canonical_hash(bound_body)
            ),
            created_at=revision.created_at,
            **bound_body,
        )
        return Ready(bound_request=bound_request, usage_entries=usage_entries)


class RequestBinder:
    """Bind one revision with an immutable set of external value resolvers."""

    def __init__(
        self,
        *,
        services: BindingServices | None = None,
        registry: CapabilityRegistry = CAPABILITY_REGISTRY,
        operation_catalogue: OperationCatalogue | None = None,
    ) -> None:
        self._services = services or BindingServices()
        self._registry = registry
        self._operation_catalogue = (
            operation_catalogue or default_operation_catalogue()
        )

    def bind(
        self,
        revision: SemanticRequestRevision,
        *,
        runtime_versions: RuntimeVersions,
    ) -> BindingDecision:
        try:
            return _RequestBinderImplementation.bind(
                revision,
                runtime_versions=runtime_versions,
                registry=self._registry,
                catalogue_resolver=self._services.catalogue_resolver,
                reform_target_selector=self._services.reform_target_selector,
                reform_validator=self._services.reform_validator,
                current_value_resolver=self._services.current_value_resolver,
                inactive_value_resolver=self._services.inactive_value_resolver,
                household_validator=self._services.household_validator,
                default_year=self._services.default_year,
                operation_catalogue=self._operation_catalogue,
            )
        except AnalysisError as exc:
            if exc.code == AnalysisErrorCode.REQUEST_UNSUPPORTED:
                return Unsupported(str(exc))
            return BindingFailed(str(exc), exc.code)


def bind_request(
    revision: SemanticRequestRevision,
    *,
    runtime_versions: RuntimeVersions,
    registry: CapabilityRegistry = CAPABILITY_REGISTRY,
    catalogue_resolver: CatalogueResolver | None = None,
    reform_target_selector: ReformTargetSelector | None = None,
    reform_validator: ReformValidator = _validate_reform,
    current_value_resolver: CurrentValueResolver = _current_parameter_values,
    inactive_value_resolver: InactiveValueResolver = _inactive_parameter_values,
    household_validator: HouseholdValidator = _validate_household,
    default_year: int | None = None,
    operation_catalogue: OperationCatalogue | None = None,
) -> BindingDecision:
    """Compatibility entry point while callers migrate to `RequestCompiler`."""

    services = BindingServices(
        catalogue_resolver=catalogue_resolver or _resolve_catalogue,
        reform_target_selector=reform_target_selector,
        reform_validator=reform_validator,
        current_value_resolver=current_value_resolver,
        inactive_value_resolver=inactive_value_resolver,
        household_validator=household_validator,
        default_year=default_year,
    )
    return RequestBinder(
        services=services,
        registry=registry,
        operation_catalogue=operation_catalogue,
    ).bind(revision, runtime_versions=runtime_versions)
