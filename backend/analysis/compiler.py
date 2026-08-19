"""Pure deterministic compilation of bound requests into immutable plans."""

from __future__ import annotations

from typing import Any, Iterable

from analysis.capabilities import (
    CAPABILITY_REGISTRY,
    CapabilityExecutionMode,
    CapabilityRegistry,
)
from analysis.common import (
    AnalysisError,
    AnalysisErrorCode,
    RuntimeVersions,
    canonical_hash,
    stable_identifier,
)
from analysis.models import (
    BoundRequest,
    ExecutionMode,
    ExecutionPlan,
    OperationConstraint,
    PlanStep,
    ResultReference,
)
from analysis.operations import OperationCatalogue, default_operation_catalogue


_IMPLIED_AGGREGATE_OPERATIONS = {
    "caseload": "count",
    "marginal_rate": "mean",
}


def _value(request: BoundRequest, field: str, default=None):
    item = request.fields.get(field)
    return item.value if item is not None else default


def _ref(step_id: str, expected_result_type: str) -> ResultReference:
    return ResultReference(
        source_step_id=step_id,
        expected_result_type=expected_result_type,
    )


def _step(
    step_id: str,
    operation: str,
    arguments: dict[str, Any],
    *,
    depends_on: tuple[str, ...] = (),
    result_binding: str,
    result_type: str,
) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        operation=operation,
        arguments=arguments,
        depends_on=depends_on,
        result_binding=result_binding,
        result_type=result_type,
    )


def _standard_steps(
    request: BoundRequest,
    registry: CapabilityRegistry,
    operation_catalogue: OperationCatalogue,
) -> tuple[PlanStep, ...]:
    kind = _value(request, "analysis_kind")
    year = _value(request, "year")
    reform = _value(request, "reform")
    if kind == "explanation":
        return ()
    if kind == "parameter_lookup":
        return (
            _step(
                "parameter",
                "get_parameter",
                {"path": _value(request, "parameter_path"), "year": year},
                result_binding="parameter",
                result_type="parameter",
            ),
        )
    if kind == "reform_validation":
        return (
            _step(
                "validate_reform",
                "validate_reform",
                {"reform": reform, "year": year},
                result_binding="reform_validation",
                result_type="reform_validation",
            ),
        )
    if kind == "household":
        arguments = {
            "people": _value(request, "people"),
            "year": year,
            **(
                {"benunit": _value(request, "benunit")}
                if _value(request, "benunit")
                else {}
            ),
            **(
                {"household": _value(request, "household")}
                if _value(request, "household")
                else {}
            ),
            **({"reform": reform} if reform else {}),
        }
        return (
            _step(
                "validate_household",
                "validate_household",
                arguments,
                result_binding="household_validation",
                result_type="household_validation",
            ),
            _step(
                "household_simulation",
                "run_household_simulation",
                arguments,
                depends_on=("validate_household",),
                result_binding="household_simulation",
                result_type="household_simulation",
            ),
        )
    if kind != "society":
        raise AnalysisError(
            AnalysisErrorCode.REQUEST_UNSUPPORTED,
            f"no standard template is registered for {kind}",
        )

    steps: list[PlanStep] = []
    simulation_dependencies: tuple[str, ...] = ()
    if reform:
        steps.append(
            _step(
                "validate_reform",
                "validate_reform",
                {"reform": reform, "year": year},
                result_binding="reform_validation",
                result_type="reform_validation",
            )
        )
        simulation_dependencies = ("validate_reform",)
    simulation_arguments: dict[str, Any] = {"year": year}
    if reform:
        simulation_arguments["reform"] = reform
    aggregate_outputs = set(request.producer_outputs).intersection(
        {"aggregate", "caseload", "marginal_rate"}
    )
    if aggregate_outputs:
        aggregate_entity = _value(request, "aggregate_entity")
        aggregate_variable = _value(request, "aggregate_variable")
        extras = {
            aggregate_entity: list(
                dict.fromkeys(
                    [
                        aggregate_variable,
                        *(
                            [_value(request, "filter_variable")]
                            if _value(request, "filter_variable")
                            else []
                        ),
                    ]
                )
            )
        }
        simulation_arguments["extra_variables"] = extras
    steps.append(
        _step(
            "society_simulation",
            "run_society_simulation",
            simulation_arguments,
            depends_on=simulation_dependencies,
            result_binding="society_simulation",
            result_type="society_simulation",
        )
    )

    operation_steps: dict[str, str] = {}
    for output in request.producer_outputs:
        if output == "chart":
            continue
        producer = registry.producer_for(kind, output)
        operation = producer.operation
        if operation == "aggregate_result":
            aggregate_operation = _IMPLIED_AGGREGATE_OPERATIONS.get(
                output,
                _value(request, "aggregate_operation"),
            )
            arguments = {
                "simulation_id": _ref("society_simulation", "society_simulation"),
                "target": _value(request, "aggregate_target", "reform"),
                "entity": _value(request, "aggregate_entity"),
                "variable": _value(request, "aggregate_variable"),
                "operation": aggregate_operation,
            }
            for name in (
                "filter_variable",
                "filter_variable_eq",
                "filter_variable_leq",
                "filter_variable_geq",
            ):
                value = _value(request, name)
                if value is not None:
                    arguments[name] = value
            step_id = f"derive_{output}"
            steps.append(
                _step(
                    step_id,
                    operation,
                    arguments,
                    depends_on=("society_simulation",),
                    result_binding=output,
                    result_type=producer.result_type,
                )
            )
            operation_steps[f"{operation}:{output}"] = step_id
            continue
        if operation in operation_steps:
            continue
        step_id = f"derive_{producer.result_type}"
        arguments: dict[str, Any] = {
            "simulation_id": _ref("society_simulation", "society_simulation")
        }
        if operation == "compute_decile_impacts" and _value(request, "decile_concept"):
            arguments["decile_concept"] = _value(request, "decile_concept")
        if operation == "compute_winners_losers" and _value(request, "comparison_basis"):
            arguments["basis"] = _value(request, "comparison_basis")
        if operation == "compute_program_breakdown" and _value(request, "programs"):
            arguments["programs"] = list(_value(request, "programs"))
        steps.append(
            _step(
                step_id,
                operation,
                arguments,
                depends_on=("society_simulation",),
                result_binding=producer.result_type,
                result_type=producer.result_type,
            )
        )
        operation_steps[operation] = step_id

    if "chart" in request.producer_outputs:
        chart_kind = _value(request, "chart_kind")
        recipe = operation_catalogue.chart_recipe(chart_kind)
        source_operation = recipe.source_operation
        source_step = operation_steps[source_operation]
        steps.append(
            _step(
                "chart",
                recipe.chart_operation,
                {
                    "chart_kind": chart_kind,
                    recipe.source_argument: _ref(
                        source_step,
                        recipe.source_result_type,
                    ),
                    **(
                        {"title": _value(request, "chart_title")}
                        if _value(request, "chart_title")
                        else {}
                    ),
                },
                depends_on=(source_step,),
                result_binding="chart",
                result_type="chart",
            )
        )
    return tuple(steps)


def _fixed_arguments(request: BoundRequest, operation: str) -> dict[str, Any]:
    if operation == "compute_decile_impacts" and _value(request, "decile_concept"):
        return {"decile_concept": _value(request, "decile_concept")}
    if operation == "compute_winners_losers" and _value(request, "comparison_basis"):
        return {"basis": _value(request, "comparison_basis")}
    if operation == "compute_program_breakdown" and _value(request, "programs"):
        return {"programs": list(_value(request, "programs"))}
    if operation == "aggregate_result":
        values = {
            "target": _value(request, "aggregate_target", "reform"),
            "entity": _value(request, "aggregate_entity"),
            "variable": _value(request, "aggregate_variable"),
            "operation": _value(request, "aggregate_operation"),
            "filter_variable": _value(request, "filter_variable"),
            "filter_variable_eq": _value(request, "filter_variable_eq"),
            "filter_variable_leq": _value(request, "filter_variable_leq"),
            "filter_variable_geq": _value(request, "filter_variable_geq"),
        }
        return {name: value for name, value in values.items() if value is not None}
    if operation == "generate_chart":
        return {
            name: value
            for name, value in {
                "chart_kind": _value(request, "chart_kind"),
                "title": _value(request, "chart_title"),
            }.items()
            if value is not None
        }
    return {}


def _exploratory_contract(
    request: BoundRequest,
    registry: CapabilityRegistry,
) -> tuple[
    tuple[str, ...],
    tuple[OperationConstraint, ...],
    tuple[str, ...],
    int,
    int,
]:
    capability = registry.capability_for("exploratory")
    profile = capability.exploratory_profile
    if profile is None:
        raise AnalysisError(
            AnalysisErrorCode.CAPABILITY_INVALID,
            "exploratory capability has no server profile",
        )
    producers = [
        registry.producer_for("exploratory", output)
        for output in request.producer_outputs
    ]
    allowed = tuple(dict.fromkeys(producer.operation for producer in producers))
    if set(allowed).difference(profile.operations):
        raise AnalysisError(
            AnalysisErrorCode.CAPABILITY_INVALID,
            "an exploratory producer is outside the server profile",
        )
    result_types_by_operation: dict[str, list[str]] = {}
    for producer in producers:
        result_types_by_operation.setdefault(producer.operation, []).append(
            producer.result_type
        )
    constraints = tuple(
        OperationConstraint(
            operation=operation,
            fixed_arguments=_fixed_arguments(request, operation),
            allowed_arguments={},
            permitted_dependencies=profile.permitted_dependencies.get(operation, ()),
            permitted_dependency_types=profile.permitted_dependency_types.get(
                operation, ()
            ),
            result_types=tuple(dict.fromkeys(result_types_by_operation[operation])),
        )
        for operation in allowed
    )
    required = tuple(
        dict.fromkeys(
            registry.producer_for("exploratory", output).result_type
            for output in request.outputs
        )
    )
    return (
        allowed,
        constraints,
        required,
        profile.max_model_iterations,
        profile.max_operation_calls,
    )


def _plan_body(
    *,
    request: BoundRequest,
    mode: ExecutionMode,
    objective: str | None,
    fixed_inputs: dict[str, Any],
    assumptions: tuple[str, ...],
    allowed_operations: tuple[str, ...],
    constraints: tuple[OperationConstraint, ...],
    required_result_types: tuple[str, ...],
    max_iterations: int,
    max_calls: int,
    steps: tuple[PlanStep, ...],
) -> dict[str, Any]:
    return {
        "schema_version": request.plan_schema_version,
        "session_id": request.session_id,
        "request_revision_id": request.request_revision_id,
        "bound_request_id": request.bound_request_id,
        "canonical_request_hash": canonical_hash(request),
        "capability_version": request.capability_version,
        "mode": mode.value,
        "objective": objective,
        "fixed_inputs": fixed_inputs,
        "catalogue_version": request.catalogue_version,
        "engine_version": request.engine_version,
        "country_package_version": request.country_package_version,
        "dataset_identifier": request.dataset_identifier,
        "assumptions": assumptions,
        "allowed_operations": allowed_operations,
        "operation_constraints": [item.model_dump(mode="json") for item in constraints],
        "required_result_types": required_result_types,
        "max_model_iterations": max_iterations,
        "max_operation_calls": max_calls,
        "steps": [item.model_dump(mode="json") for item in steps],
    }


class ExecutionPlanCompiler:
    """Compile exact calculation instructions without changing request state."""

    @staticmethod
    def compile(
        request: BoundRequest,
        registry: CapabilityRegistry = CAPABILITY_REGISTRY,
        operation_catalogue: OperationCatalogue | None = None,
    ) -> ExecutionPlan:
        operation_catalogue = operation_catalogue or default_operation_catalogue()
        operation_catalogue.validate(registry)
        if request.capability_version != registry.version:
            raise AnalysisError(
                AnalysisErrorCode.PLAN_STALE,
                "bound request capability version is stale",
            )
        kind = _value(request, "analysis_kind")
        capability = registry.capability_for(kind)
        objective = None
        fixed_inputs: dict[str, Any] = {}
        constraints: tuple[OperationConstraint, ...] = ()
        required_result_types: tuple[str, ...] = ()
        max_iterations = 0
        max_calls = 0
        if capability.execution_mode in {
            CapabilityExecutionMode.EXPLANATION,
            CapabilityExecutionMode.STANDARD,
        }:
            mode = (
                ExecutionMode.EXPLANATION
                if capability.execution_mode == CapabilityExecutionMode.EXPLANATION
                else ExecutionMode.STANDARD
            )
            steps = _standard_steps(request, registry, operation_catalogue)
            allowed = tuple(step.operation for step in steps)
        else:
            mode = ExecutionMode.EXPLORATORY
            objective = str(_value(request, "objective", "")).strip()
            if not objective:
                raise AnalysisError(
                    AnalysisErrorCode.PLAN_INVALID,
                    "exploratory analysis has no objective",
                )
            (
                allowed,
                constraints,
                required_result_types,
                max_iterations,
                max_calls,
            ) = _exploratory_contract(request, registry)
            fixed_inputs = {
                "year": _value(request, "year"),
                "reform": _value(request, "reform"),
                "dataset_identifier": request.dataset_identifier,
            }
            steps = (
                _step(
                    "society_simulation",
                    "run_society_simulation",
                    {
                        name: value
                        for name, value in fixed_inputs.items()
                        if name in {"year", "reform"} and value is not None
                    },
                    result_binding="society_simulation",
                    result_type="society_simulation",
                ),
            )
        assumptions = tuple(str(value) for value in (_value(request, "assumptions", ()) or ()))
        body = _plan_body(
            request=request,
            mode=mode,
            objective=objective,
            fixed_inputs=fixed_inputs,
            assumptions=assumptions,
            allowed_operations=allowed,
            constraints=constraints,
            required_result_types=required_result_types,
            max_iterations=max_iterations,
            max_calls=max_calls,
            steps=steps,
        )
        plan_hash = canonical_hash(body)
        plan = ExecutionPlan(
            plan_id=stable_identifier("plan", request.bound_request_id, plan_hash),
            plan_hash=plan_hash,
            created_at=request.created_at,
            **body,
        )
        validate_plan(plan)
        return plan


def compile_plan(
    request: BoundRequest,
    registry: CapabilityRegistry = CAPABILITY_REGISTRY,
    operation_catalogue: OperationCatalogue | None = None,
) -> ExecutionPlan:
    """Compatibility entry point while callers migrate to `RequestCompiler`."""

    return ExecutionPlanCompiler.compile(
        request,
        registry,
        operation_catalogue or default_operation_catalogue(),
    )


def _walk_result_references(value: Any) -> Iterable[ResultReference]:
    if isinstance(value, ResultReference):
        yield value
    elif isinstance(value, dict):
        if "source_step_id" in value and set(value).issubset(
            {"source_step_id", "expected_result_type"}
        ):
            yield ResultReference.model_validate(value)
        else:
            for item in value.values():
                yield from _walk_result_references(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk_result_references(item)


def validate_plan(plan: ExecutionPlan) -> None:
    body = plan.model_dump(mode="json", exclude={"plan_id", "plan_hash", "created_at"})
    if canonical_hash(body) != plan.plan_hash:
        raise AnalysisError(AnalysisErrorCode.PLAN_INVALID, "plan hash does not match")
    step_ids = [step.step_id for step in plan.steps]
    if len(step_ids) != len(set(step_ids)):
        raise AnalysisError(AnalysisErrorCode.PLAN_INVALID, "plan step identifiers repeat")
    steps_by_id = {step.step_id: step for step in plan.steps}
    graph: dict[str, set[str]] = {}
    for step in plan.steps:
        dependencies = set(step.depends_on)
        if not dependencies.issubset(steps_by_id) or step.step_id in dependencies:
            raise AnalysisError(
                AnalysisErrorCode.PLAN_INVALID,
                "plan contains an invalid step dependency",
            )
        for reference in _walk_result_references(step.arguments):
            if reference.source_step_id not in dependencies:
                raise AnalysisError(
                    AnalysisErrorCode.PLAN_INVALID,
                    "result reference is not declared as a prerequisite",
                )
            source = steps_by_id[reference.source_step_id]
            if (
                reference.expected_result_type is not None
                and reference.expected_result_type != source.result_type
            ):
                raise AnalysisError(
                    AnalysisErrorCode.PLAN_INVALID,
                    "result reference type disagrees with its producer",
                )
        graph[step.step_id] = dependencies
    remaining = {name: set(dependencies) for name, dependencies in graph.items()}
    while remaining:
        ready = {name for name, dependencies in remaining.items() if not dependencies}
        if not ready:
            raise AnalysisError(AnalysisErrorCode.PLAN_INVALID, "plan contains a cycle")
        remaining = {
            name: dependencies.difference(ready)
            for name, dependencies in remaining.items()
            if name not in ready
        }
    if plan.mode == ExecutionMode.STANDARD and plan.allowed_operations != tuple(
        step.operation for step in plan.steps
    ):
        raise AnalysisError(
            AnalysisErrorCode.PLAN_INVALID,
            "standard plan operation list does not match its steps",
        )
    if plan.mode == ExecutionMode.EXPLORATORY:
        if not plan.required_result_types:
            raise AnalysisError(
                AnalysisErrorCode.PLAN_INVALID,
                "exploratory plan has no required result types",
            )
        constrained = tuple(item.operation for item in plan.operation_constraints)
        if constrained != plan.allowed_operations:
            raise AnalysisError(
                AnalysisErrorCode.PLAN_INVALID,
                "exploratory constraints do not match permitted operations",
            )
        producible = {
            result_type
            for constraint in plan.operation_constraints
            for result_type in constraint.result_types
        }
        missing = set(plan.required_result_types).difference(producible)
        if missing:
            raise AnalysisError(
                AnalysisErrorCode.PLAN_INVALID,
                "exploratory plan cannot produce required results: "
                + ", ".join(sorted(missing)),
            )
    elif plan.required_result_types:
        raise AnalysisError(
            AnalysisErrorCode.PLAN_INVALID,
            "non-exploratory plan declares exploratory result requirements",
        )


def plan_is_stale(
    plan: ExecutionPlan,
    *,
    current_bound_request_id: str,
    versions: RuntimeVersions,
    capability_version: str = CAPABILITY_REGISTRY.version,
) -> bool:
    return any(
        (
            plan.bound_request_id != current_bound_request_id,
            plan.capability_version != capability_version,
            plan.catalogue_version != versions.catalogue_version,
            plan.engine_version != versions.engine_version,
            plan.country_package_version != versions.country_package_version,
            plan.dataset_identifier != versions.dataset_identifier,
            plan.schema_version != versions.plan_schema_version,
        )
    )
