"""Model catalog discovery helpers for policyengine.py UK."""

from __future__ import annotations

from dataclasses import asdict
from difflib import get_close_matches
from typing import Any

from engine.py_runtime import list_dataset_specs, uk_model_version
from engine.reforms import COMMON_REFORM_TARGETS, search_reform_targets
from engine.serialization import json_safe


def _matches(query: str, *values: str | None) -> bool:
    if not query:
        return True
    q = query.lower()
    haystack = " ".join(v or "" for v in values).lower()
    if q in haystack:
        return True
    return bool(get_close_matches(q, [v.lower() for v in values if v], n=1, cutoff=0.65))


def _default_output_entities(model: Any, name: str) -> list[str]:
    return [
        entity
        for entity, variables in model.entity_variables.items()
        if name in variables
    ]


def _variable_item(name: str, variable: Any, model: Any) -> dict[str, Any]:
    default_output_entities = _default_output_entities(model, name)
    return {
        "name": name,
        "label": getattr(variable, "label", None),
        "entity": getattr(variable, "entity", None),
        "description": getattr(variable, "description", None),
        "definition_period": getattr(variable, "definition_period", None),
        "value_type": getattr(getattr(variable, "value_type", None), "__name__", None)
        or str(getattr(variable, "value_type", "")),
        "default_value": json_safe(getattr(variable, "default_value", None)),
        "possible_values": json_safe(getattr(variable, "possible_values", None)),
        "is_default_society_output": bool(default_output_entities),
        "default_output_entities": default_output_entities,
    }


def list_entities() -> dict[str, Any]:
    model = uk_model_version()
    entities: dict[str, dict[str, Any]] = {}
    for variable in model.variables_by_name.values():
        entity = getattr(variable, "entity", None)
        if not entity:
            continue
        entities.setdefault(entity, {"name": entity, "variable_count": 0})
        entities[entity]["variable_count"] += 1
    return {"status": "success", "entities": list(entities.values())}


def search_variables(
    query: str = "",
    entity: str | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    model = uk_model_version()
    rows: list[dict[str, Any]] = []
    for name, variable in model.variables_by_name.items():
        item = _variable_item(name, variable, model)
        if entity and item["entity"] != entity:
            continue
        if not _matches(query, name, item.get("label"), item.get("description")):
            continue
        rows.append(item)
        if len(rows) >= limit:
            break
    return {
        "status": "success",
        "query": query,
        "entity": entity,
        "variables": rows,
    }


def get_variable(name: str) -> dict[str, Any]:
    model = uk_model_version()
    variable = model.variables_by_name.get(name)
    if variable is None:
        suggestions = get_close_matches(name, list(model.variables_by_name), n=5, cutoff=0.6)
        return {"status": "error", "error": f"Unknown variable: {name}", "suggestions": suggestions}
    return {
        "status": "success",
        "variable": _variable_item(name, variable, model),
    }


def list_society_output_variables(
    entity: str | None = None,
) -> dict[str, Any]:
    """List variables materialized by a default policyengine.py UK simulation."""

    model = uk_model_version()
    defaults = model.entity_variables
    if entity is not None and entity not in defaults:
        return {
            "status": "error",
            "error": f"Unknown society output entity: {entity}",
            "available_entities": list(defaults),
        }

    selected = (
        {entity: list(defaults[entity])}
        if entity is not None
        else {name: list(variables) for name, variables in defaults.items()}
    )
    return {
        "status": "success",
        "entity": entity,
        "default_variables_by_entity": selected,
        "default_variable_count": sum(len(variables) for variables in selected.values()),
        "extra_variables_contract": (
            "Do not put these default variables in extra_variables. For any other "
            "required output or aggregate filter, first verify its exact name with "
            "search_variables or get_variable, then add it under the variable's "
            "declared entity. extra_variables cannot define new variables, "
            "expressions, aliases, or derived concepts."
        ),
    }


def _parameter_value(parameter: Any, year: int) -> Any:
    core = getattr(parameter, "_core_param", None)
    if core is None:
        return None
    for period in (year, str(year), f"{year}-01-01"):
        try:
            return core(period)
        except Exception:
            continue
    return None


def _parameter_item(path: str, parameter: Any, year: int | None = None) -> dict[str, Any]:
    item = {
        "path": path,
        "label": getattr(parameter, "label", None),
        "description": getattr(parameter, "description", None),
        "unit": getattr(parameter, "unit", None),
        "aliases": list(COMMON_REFORM_TARGETS.get(path, ())),
    }
    if year is not None:
        item["year"] = year
        item["value"] = json_safe(_parameter_value(parameter, year))
    return item


def search_parameters(query: str = "", limit: int = 25) -> dict[str, Any]:
    model = uk_model_version()
    rows: list[dict[str, Any]] = []
    for path, parameter in model.parameters_by_name.items():
        item = _parameter_item(path, parameter)
        if not _matches(query, path, item.get("label"), item.get("description"), " ".join(item["aliases"])):
            continue
        rows.append(item)
        if len(rows) >= limit:
            break
    return {"status": "success", "query": query, "parameters": rows}


def get_parameter(path: str, year: int) -> dict[str, Any]:
    model = uk_model_version()
    parameter = model.parameters_by_name.get(path)
    if parameter is None:
        suggestions = get_close_matches(path, list(model.parameters_by_name), n=5, cutoff=0.6)
        return {"status": "error", "error": f"Unknown parameter: {path}", "suggestions": suggestions}
    return {"status": "success", "parameter": _parameter_item(path, parameter, year)}


def list_datasets() -> dict[str, Any]:
    return {
        "status": "success",
        "datasets": [asdict(spec) for spec in list_dataset_specs()],
    }


def list_household_input_variables(entity: str | None = None) -> dict[str, Any]:
    result = search_variables(query="", entity=entity, limit=500)
    result["input_contract"] = (
        "policyengine.py accepts any known variable on its declared entity as "
        "a synthetic-household override."
    )
    return result


def list_reform_targets(query: str = "", limit: int = 25) -> dict[str, Any]:
    return {
        "status": "success",
        "query": query,
        "targets": search_reform_targets(query=query, limit=limit),
    }


def supported_outputs(scope: str | None = None) -> list[dict[str, str]]:
    rows = [
        {"scope": "household", "name": "household_net_income", "description": "Synthetic household net income."},
        {"scope": "household", "name": "income_tax", "description": "Synthetic household/person income tax."},
        {"scope": "household", "name": "axes_series", "description": "One complete numeric synthetic-household input sweep."},
        {"scope": "society", "name": "simulation_handle", "description": "A policyengine.py baseline/reform Simulation pair."},
        {"scope": "derivative", "name": "budgetary_impact", "description": "Weighted tax, benefit-spending, and net fiscal impacts."},
        {"scope": "derivative", "name": "program_statistics", "description": "Official programme totals, caseloads, winners, and losers."},
        {"scope": "derivative", "name": "decile_impacts", "description": "Official absolute and relative income changes by decile."},
        {"scope": "derivative", "name": "winners_losers", "description": "Official people-weighted intra-decile impacts."},
        {"scope": "derivative", "name": "poverty", "description": "Official UK poverty rates overall and by age."},
        {"scope": "derivative", "name": "inequality", "description": "Official UK Gini and income-share metrics."},
        {"scope": "artifact", "name": "chart", "description": "Deterministic app-v2-style chart specs."},
    ]
    return [row for row in rows if scope is None or row["scope"] == scope]


def list_supported_outputs(scope: str | None = None) -> dict[str, Any]:
    return {"status": "success", "scope": scope, "outputs": supported_outputs(scope)}
