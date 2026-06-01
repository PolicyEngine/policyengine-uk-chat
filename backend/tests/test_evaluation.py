from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

import evaluation.runner as runner
from evaluation.graders import grade_output, grade_text, grade_tool_calls
from evaluation.loaders import load_case_file
from evaluation.runner import run_eval
from evaluation.schemas import (
    CaseSkip,
    ModelToolCall,
    NumericExpectation,
    OutputExpectation,
    TextExpectation,
    ToolContractCase,
    ToolCallExpectation,
)
from evaluation.sync_policyengine_uk import render_generated_cases


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_loads_yaml_cases_with_typed_schemas():
    cases = load_case_file(REPO_ROOT / "evals" / "cases" / "trajectory" / "core.yaml")

    assert cases
    assert {case.suite for case in cases} == {"trajectory"}
    assert cases[0].expected_tools[0].name == "calculate_household"


def test_grade_output_supports_partial_paths_and_numeric_tolerance():
    actual = {
        "status": "success",
        "rows": [{"value": 10.01, "extra": "ok"}],
    }
    expectation = OutputExpectation(
        contains={"status": "success"},
        required_paths=["rows.0.value"],
        absent_paths=["rows.0.secret"],
        numeric=[NumericExpectation(path="rows.0.value", equals=10.0, tolerance=0.02)],
    )

    assert grade_output(actual, expectation) == []


def test_grade_output_supports_collection_sum_numeric_paths():
    actual = {
        "person": [
            {"baseline_income_tax": 1.25},
            {"baseline_income_tax": 2.75},
        ],
    }
    expectation = OutputExpectation(
        numeric=[
            NumericExpectation(
                path="person[].baseline_income_tax",
                equals=4.0,
                tolerance=0.0,
            )
        ],
    )

    assert grade_output(actual, expectation) == []


def test_grade_tool_calls_matches_ordered_semantic_expectations():
    actual = [
        ModelToolCall(name="validate_reform", input={"reform": {"income_tax": {"personal_allowance": 15000}}}),
        ModelToolCall(name="calculate_household", input={"year": 2025, "person": [], "benunit": [], "household": []}),
    ]
    expected = [
        ToolCallExpectation(
            name="validate_reform",
            input_contains={"reform": {"income_tax": {"personal_allowance": 15000}}},
        ),
        ToolCallExpectation(name="calculate_household", required_input_paths=["person"]),
    ]

    assert grade_tool_calls(actual, expected, forbidden_tools=["analyse_microdata"]) == []


def test_grade_text_checks_required_forbidden_and_grounded_numbers():
    expectation = TextExpectation(
        required=["illustrative", "£200"],
        forbidden=["fair", "generous"],
        grounded_numbers=True,
        allowed_numbers=[200, 2025],
    )

    assert grade_text("In 2025, this illustrative household changes by £200.", expectation) == []


def test_offline_eval_runs_seed_trajectory_and_answer_cases_without_reports():
    report = run_eval(
        suites=["trajectory", "answer"],
        mode="offline",
        write_reports=False,
    )

    assert report.failed == 0
    assert report.passed == 8


def test_skipped_tool_contract_cases_require_source_metadata():
    with pytest.raises(ValidationError):
        ToolContractCase(
            id="missing_source",
            description="Skipped cases need traceable upstream source metadata.",
            tool_name="calculate_household",
            input={},
            skip=CaseSkip(
                code="compiled_coverage_gap",
                reason="compiled gap",
                remove_when="compiled supports this case",
            ),
        )


def test_skipped_tool_contract_case_reports_skip_without_execution(tmp_path, monkeypatch):
    case_file = tmp_path / "cases.yaml"
    case_file.write_text(
        yaml.safe_dump(
            {
                "cases": [
                    {
                        "id": "compiled_gap_case",
                        "suite": "tool_contract",
                        "description": "Skipped upstream case.",
                        "source": {
                            "package": "policyengine-uk",
                            "version": "1.0.0",
                            "path": "tests/policy/example.yaml",
                            "name": "Skipped case",
                        },
                        "skip": {
                            "code": "compiled_coverage_gap",
                            "reason": "compiled does not expose this behavior",
                            "remove_when": "compiled exposes this behavior",
                        },
                        "tool_name": "calculate_household",
                        "input": {},
                    }
                ]
            }
        )
    )

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("skipped cases must not execute tools")

    monkeypatch.setattr(runner, "_case_paths", lambda _suites: [case_file])
    monkeypatch.setattr(runner, "execute_tool", fail_if_called)

    report = runner.run_eval(
        suites=["tool_contract"],
        mode="offline",
        write_reports=False,
    )

    assert report.skipped == 1
    assert report.failed == 0
    assert "compiled_coverage_gap" in report.results[0].errors[0]


def test_policyengine_uk_generated_cases_validate():
    cases = load_case_file(REPO_ROOT / "evals" / "cases" / "tool_contract" / "policyengine_uk.generated.yaml")

    assert len(cases) >= 4
    assert any(isinstance(case, ToolContractCase) and case.skip for case in cases)
    assert all(case.source for case in cases if case.id.startswith("policyengine_uk_"))


def test_policyengine_uk_sync_renders_active_and_skipped_cases(tmp_path):
    package_root = tmp_path / "policyengine_uk"
    policy_dir = package_root / "tests" / "policy"
    policy_dir.mkdir(parents=True)
    (policy_dir / "sample.yaml").write_text(
        yaml.safe_dump(
            [
                {
                    "name": "Active case",
                    "period": 2025,
                    "absolute_error_margin": 2,
                    "input": {"employment_income": 10000},
                    "output": {"income_tax": 100},
                },
                {
                    "name": "Skipped case",
                    "period": 2025,
                    "input": {"employment_income": 20000},
                    "output": {"income_tax": 200},
                },
            ],
            sort_keys=False,
        )
    )
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "source_package": "policyengine_uk",
                "source_distribution": "policyengine-uk",
                "compiled_distribution": "policyengine-uk-compiled",
                "cases": [
                    {
                        "id": "active_case",
                        "path": "tests/policy/sample.yaml",
                        "name": "Active case",
                        "output_map": {"income_tax": "person[].baseline_income_tax"},
                    },
                    {
                        "id": "skipped_case",
                        "path": "tests/policy/sample.yaml",
                        "name": "Skipped case",
                        "skip": {
                            "code": "compiled_coverage_gap",
                            "reason": "compiled gap",
                            "remove_when": "compiled supports it",
                        },
                    },
                ],
            },
            sort_keys=False,
        )
    )

    rendered = render_generated_cases(
        manifest,
        package_root=package_root,
        package_version="1.2.3",
        compiled_version="4.5.6",
    )
    generated = yaml.safe_load(rendered)

    assert generated["source"]["version"] == "1.2.3"
    assert generated["source"]["compiled_version"] == "4.5.6"
    assert generated["cases"][0]["input"]["person"][0]["employment_income"] == 10000
    assert generated["cases"][0]["expect"]["numeric"][0]["path"] == "person[].baseline_income_tax"
    assert generated["cases"][0]["expect"]["numeric"][0]["tolerance"] == 2.0
    assert generated["cases"][1]["skip"]["code"] == "compiled_coverage_gap"
