"""Generate source-synced tool-contract evals from policyengine-uk YAML tests."""

from __future__ import annotations

import argparse
import importlib
import sys
from importlib import metadata
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = REPO_ROOT / "evals" / "sources" / "policyengine_uk.yaml"
DEFAULT_OUTPUT = REPO_ROOT / "evals" / "cases" / "tool_contract" / "policyengine_uk.generated.yaml"

BENUNIT_KEYS = {
    "is_lone_parent",
    "rent_monthly",
    "on_uc",
    "on_legacy",
    "would_claim_uc",
    "would_claim_cb",
    "would_claim_child_benefit",
    "would_claim_hb",
    "would_claim_pc",
    "would_claim_ctc",
    "would_claim_wtc",
    "would_claim_is",
    "would_claim_esa",
    "would_claim_jsa",
}

HOUSEHOLD_KEYS = {
    "region",
    "rent_annual",
    "council_tax_annual",
    "weight",
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


def _flat_input_to_situation(flat_input: dict[str, Any]) -> dict[str, Any]:
    person_fields: dict[str, Any] = {}
    benunit_fields: dict[str, Any] = {"members": ["you"]}
    household_fields: dict[str, Any] = {"members": ["you"]}

    for key, value in flat_input.items():
        if key in BENUNIT_KEYS:
            benunit_fields[key] = value
        elif key in HOUSEHOLD_KEYS:
            household_fields[key] = value
        else:
            person_fields[key] = value

    return {
        "people": {"you": person_fields},
        "benunits": {"benunit": benunit_fields},
        "households": {"household": household_fields},
    }


def _to_tool_input(raw_input: dict[str, Any], year: int) -> dict[str, Any]:
    if any(key in raw_input for key in ("people", "benunits", "households")):
        situation = raw_input
    else:
        situation = _flat_input_to_situation(raw_input)

    people = situation.get("people") or {"you": {}}
    people_ids = list(people.keys())
    benunits = situation.get("benunits") or {"benunit": {"members": people_ids}}
    households = situation.get("households") or {"household": {"members": people_ids}}

    if not people_ids:
        raise ValueError("upstream case must include at least one person")

    first_benunit_id = next(iter(benunits.keys()))
    first_household_id = next(iter(households.keys()))

    person_to_benunit: dict[str, str] = {}
    for benunit_id, fields in benunits.items():
        for member in (fields or {}).get("members", []):
            person_to_benunit[member] = benunit_id
    for person_id in people_ids:
        person_to_benunit.setdefault(person_id, first_benunit_id)

    person_to_household: dict[str, str] = {}
    for household_id, fields in households.items():
        for member in (fields or {}).get("members", []):
            person_to_household[member] = household_id
    for person_id in people_ids:
        person_to_household.setdefault(person_id, first_household_id)

    person_records = []
    for person_id, fields in people.items():
        record = {
            "person_id": person_id,
            "benunit_id": person_to_benunit[person_id],
            "household_id": person_to_household[person_id],
        }
        for key, value in (fields or {}).items():
            if key != "members":
                record[key] = _resolve_period_value(value, year)
        person_records.append(record)

    benunit_records = []
    for benunit_id, fields in benunits.items():
        members = (fields or {}).get("members", [])
        household_id = first_household_id
        for member in members:
            if member in person_to_household:
                household_id = person_to_household[member]
                break
        record = {"benunit_id": benunit_id, "household_id": household_id}
        for key, value in (fields or {}).items():
            if key != "members":
                record[key] = _resolve_period_value(value, year)
        benunit_records.append(record)

    household_records = []
    for household_id, fields in households.items():
        record = {"household_id": household_id}
        for key, value in (fields or {}).items():
            if key != "members":
                record[key] = _resolve_period_value(value, year)
        household_records.append(record)

    return {
        "year": year,
        "person": person_records,
        "benunit": benunit_records,
        "household": household_records,
    }


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
) -> list[dict[str, Any]]:
    expectations = []
    margin = float(upstream_case.get("absolute_error_margin", 1.0))
    for variable, expected in (upstream_case.get("output") or {}).items():
        if variable not in output_map:
            raise ValueError(f"{upstream_case['name']!r}: no output mapping for {variable!r}")
        try:
            expected_value = float(expected)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{upstream_case['name']!r}: expected value for {variable!r} is not numeric"
            ) from exc
        expectations.append(
            {
                "path": output_map[variable],
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
    compiled_version: str | None = None,
) -> str:
    manifest = yaml.safe_load(manifest_path.read_text()) or {}
    package_name = manifest.get("source_package", "policyengine_uk")
    distribution_name = manifest.get("source_distribution", "policyengine-uk")
    compiled_distribution = manifest.get("compiled_distribution", "policyengine-uk-compiled")

    package_root = package_root or _package_root(package_name)
    package_version = package_version or _distribution_version(distribution_name)
    compiled_version = compiled_version or _distribution_version(compiled_distribution)

    generated_cases = []
    for entry in manifest.get("cases", []):
        upstream_case = _load_upstream_case(package_root, entry["path"], entry["name"])
        year = int(upstream_case["period"])
        generated_case: dict[str, Any] = {
            "id": entry["id"],
            "suite": "tool_contract",
            "description": f"Imported from {distribution_name}: {entry['path']} :: {entry['name']}",
            "tags": ["policyengine_uk", "source_synced"],
            "requirements": ["compiled"],
            "source": {
                "package": distribution_name,
                "version": package_version,
                "path": entry["path"],
                "name": entry["name"],
            },
            "tool_name": "calculate_household",
            "input": _to_tool_input(upstream_case.get("input") or {}, year),
        }

        if "skip" in entry:
            generated_case["skip"] = entry["skip"]
        else:
            generated_case["expect"] = {
                "numeric": _numeric_expectations(upstream_case, entry.get("output_map") or {})
            }
        generated_cases.append(generated_case)

    payload = {
        "source": {
            "generated_from": _display_path(manifest_path),
            "package": distribution_name,
            "version": package_version,
            "compiled_package": compiled_distribution,
            "compiled_version": compiled_version,
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
