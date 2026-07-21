"""Generate source-synced tool-contract evals from policyengine-uk YAML tests."""

from __future__ import annotations

import argparse
import ast
import importlib
import math
import operator
import sys
from importlib import metadata
from pathlib import Path
from typing import Any, Mapping

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = REPO_ROOT / "evals" / "sources" / "policyengine_uk.yaml"
DEFAULT_OUTPUT = REPO_ROOT / "evals" / "cases" / "tool_contract" / "policyengine_uk.generated.yaml"

_ARITHMETIC_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _package_root(package_name: str) -> Path:
    module = importlib.import_module(package_name)
    if module.__file__ is None:
        raise RuntimeError(f"{package_name} has no package file")
    return Path(module.__file__).resolve().parent


def _distribution_version(distribution_name: str) -> str:
    try:
        return metadata.version(distribution_name)
    except metadata.PackageNotFoundError:
        return "unknown"


def _variable_entities(package_name: str) -> dict[str, str]:
    system_module = importlib.import_module(f"{package_name}.system")
    system = system_module.system
    return {
        name: variable.entity.key
        for name, variable in system.variables.items()
    }


def _resolve_period_value(value: Any, year: int) -> Any:
    if not isinstance(value, dict):
        return value

    year_str = str(year)
    if year_str in value:
        return value[year_str]
    for key, item in value.items():
        if str(key)[:4] == year_str:
            return item

    candidates: list[tuple[int, Any]] = []
    for key, item in value.items():
        try:
            candidates.append((int(str(key)[:4]), item))
        except (TypeError, ValueError):
            continue
    if not candidates:
        return next(iter(value.values()))
    candidates.sort()
    earlier = [item for candidate_year, item in candidates if candidate_year <= year]
    return earlier[-1] if earlier else candidates[0][1]


def _flat_input_to_situation(
    flat_input: dict[str, Any],
    variable_entities: Mapping[str, str],
) -> dict[str, Any]:
    person_fields: dict[str, Any] = {}
    benunit_fields: dict[str, Any] = {"members": ["you"]}
    household_fields: dict[str, Any] = {"members": ["you"]}

    for key, value in flat_input.items():
        entity = variable_entities.get(key)
        if entity == "person":
            person_fields[key] = value
        elif entity == "benunit":
            benunit_fields[key] = value
        elif entity == "household":
            household_fields[key] = value
        else:
            raise ValueError(f"upstream input variable {key!r} is not defined by policyengine-uk")

    return {
        "people": {"you": person_fields},
        "benunits": {"benunit": benunit_fields},
        "households": {"household": household_fields},
    }


def _to_tool_input(
    raw_input: dict[str, Any],
    year: int,
    variable_entities: Mapping[str, str],
) -> dict[str, Any]:
    parameter_overrides = {
        key: _resolve_period_value(value, year)
        for key, value in raw_input.items()
        if key not in variable_entities
        and key not in {"people", "benunits", "households", "axes"}
        and "." in key
    }
    model_input = {
        key: value
        for key, value in raw_input.items()
        if key not in parameter_overrides
    }

    if any(key in model_input for key in ("people", "benunits", "households")):
        situation = model_input
    else:
        situation = _flat_input_to_situation(model_input, variable_entities)

    people = situation.get("people") or {"you": {}}
    people_ids = list(people.keys())
    benunits = situation.get("benunits") or {"benunit": {"members": people_ids}}
    households = situation.get("households") or {"household": {"members": people_ids}}

    if not people_ids:
        raise ValueError("upstream case must include at least one person")

    first_benunit_id = next(iter(benunits.keys()))
    first_household_id = next(iter(households.keys()))

    person_records = []
    for fields in people.values():
        record = {}
        for key, value in (fields or {}).items():
            if key != "members":
                record[key] = _resolve_period_value(value, year)
        person_records.append(record)

    benunit_fields = {
        key: _resolve_period_value(value, year)
        for key, value in (benunits.get(first_benunit_id) or {}).items()
        if key != "members"
    }
    household_fields = {
        key: _resolve_period_value(value, year)
        for key, value in (households.get(first_household_id) or {}).items()
        if key != "members"
    }

    tool_input = {
        "year": year,
        "people": person_records,
        "benunit": benunit_fields,
        "household": household_fields,
    }
    if parameter_overrides:
        tool_input["reform"] = parameter_overrides
    return tool_input


def _evaluate_numeric_expression(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("boolean values are not numeric expectations")
    if isinstance(value, (int, float)):
        result = float(value)
    elif isinstance(value, str):
        tree = ast.parse(value, mode="eval")

        def evaluate(node: ast.AST) -> float:
            if isinstance(node, ast.Expression):
                return evaluate(node.body)
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                return float(node.value)
            if isinstance(node, ast.BinOp) and type(node.op) in _ARITHMETIC_OPERATORS:
                return float(
                    _ARITHMETIC_OPERATORS[type(node.op)](
                        evaluate(node.left),
                        evaluate(node.right),
                    )
                )
            if isinstance(node, ast.UnaryOp) and type(node.op) in _ARITHMETIC_OPERATORS:
                return float(_ARITHMETIC_OPERATORS[type(node.op)](evaluate(node.operand)))
            raise ValueError(f"unsupported numeric expression: {value!r}")

        result = evaluate(tree)
    else:
        raise ValueError(f"expected a number or arithmetic expression, got {type(value).__name__}")

    if not math.isfinite(result):
        raise ValueError(f"numeric expectation must be finite, got {value!r}")
    return result


def _load_upstream_case(package_root: Path, relative_path: str, case_name: str) -> dict[str, Any]:
    source_path = package_root / relative_path
    if not source_path.exists():
        raise FileNotFoundError(f"Missing policyengine-uk source test: {relative_path}")

    cases = yaml.safe_load(source_path.read_text()) or []
    for case in cases:
        if case.get("name") == case_name:
            return case
    raise ValueError(f"{relative_path}: missing case {case_name!r}")


def _numeric_expectations(
    upstream_case: dict[str, Any],
    output_map: dict[str, str],
    *,
    reform_applied: bool,
) -> list[dict[str, Any]]:
    expectations = []
    margin = float(upstream_case.get("absolute_error_margin", 1.0))
    for variable, expected in (upstream_case.get("output") or {}).items():
        if variable not in output_map:
            raise ValueError(f"{upstream_case['name']!r}: no output mapping for {variable!r}")
        try:
            expected_value = _evaluate_numeric_expression(expected)
        except (SyntaxError, TypeError, ValueError, ZeroDivisionError) as exc:
            raise ValueError(
                f"{upstream_case['name']!r}: expected value for {variable!r} is not numeric"
            ) from exc
        expectations.append(
            {
                "path": (
                    f"reform.{output_map[variable]}"
                    if reform_applied
                    else output_map[variable]
                ),
                "equals": expected_value,
                "tolerance": margin,
            }
        )
    return expectations


def render_generated_cases(
    manifest_path: Path = DEFAULT_MANIFEST,
    *,
    package_root: Path | None = None,
    package_version: str | None = None,
    runtime_version: str | None = None,
    variable_entities: Mapping[str, str] | None = None,
) -> str:
    manifest = yaml.safe_load(manifest_path.read_text()) or {}
    package_name = manifest.get("source_package", "policyengine_uk")
    distribution_name = manifest.get("source_distribution", "policyengine-uk")
    runtime_distribution = manifest.get("runtime_distribution", "policyengine")

    package_root = package_root or _package_root(package_name)
    package_version = package_version or _distribution_version(distribution_name)
    runtime_version = runtime_version or _distribution_version(runtime_distribution)
    variable_entities = variable_entities or _variable_entities(package_name)

    generated_cases = []
    for entry in manifest.get("cases", []):
        upstream_case = _load_upstream_case(package_root, entry["path"], entry["name"])
        year = int(upstream_case["period"])
        generated_case: dict[str, Any] = {
            "id": entry["id"],
            "suite": "tool_contract",
            "description": f"Imported from {distribution_name}: {entry['path']} :: {entry['name']}",
            "tags": ["policyengine_uk", "source_synced"],
            "requirements": ["policyengine_py"],
            "source": {
                "package": distribution_name,
                "version": package_version,
                "path": entry["path"],
                "name": entry["name"],
            },
            "tool_name": "run_household_simulation",
            "input": _to_tool_input(
                upstream_case.get("input") or {},
                year,
                variable_entities,
            ),
        }

        if "skip" in entry:
            generated_case["skip"] = entry["skip"]
        else:
            output_map = entry.get("output_map") or {}
            generated_case["input"]["extra_variables"] = list(output_map)
            generated_case["expect"] = {
                "numeric": _numeric_expectations(
                    upstream_case,
                    output_map,
                    reform_applied=bool(generated_case["input"].get("reform")),
                )
            }
        generated_cases.append(generated_case)

    payload = {
        "source": {
            "generated_from": _display_path(manifest_path),
            "package": distribution_name,
            "version": package_version,
            "runtime_package": runtime_distribution,
            "runtime_version": runtime_version,
        },
        "cases": generated_cases,
    }
    return yaml.safe_dump(payload, sort_keys=False)


def _output_path(manifest_path: Path, explicit_output: Path | None) -> Path:
    if explicit_output is not None:
        return explicit_output
    manifest = yaml.safe_load(manifest_path.read_text()) or {}
    if "generated_file" in manifest:
        return REPO_ROOT / manifest["generated_file"]
    return DEFAULT_OUTPUT


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync policyengine-uk source eval cases")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--sync", action="store_true", help="Rewrite the generated eval file")
    mode.add_argument("--check", action="store_true", help="Check that the generated eval file is fresh")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    output_path = _output_path(args.manifest, args.output)
    generated = render_generated_cases(args.manifest)

    if args.sync:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(generated)
        print(f"Wrote {_display_path(output_path)}")
        return 0

    if not output_path.exists():
        print(f"{_display_path(output_path)} is missing; run make sync-policyengine-uk-evals")
        return 1
    current = output_path.read_text()
    if current != generated:
        print(f"{_display_path(output_path)} is stale; run make sync-policyengine-uk-evals")
        return 1
    print(f"{_display_path(output_path)} is up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
