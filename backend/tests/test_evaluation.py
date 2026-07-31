from copy import deepcopy
from datetime import date
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator
from pydantic import ValidationError

import eval.runner as runner
from eval.graders import grade_output, grade_text, grade_tool_calls
from eval.loaders import load_case_file
from eval.reporting import render_markdown
from eval.runner import run_eval
from eval.schemas import (
    AnswerCase,
    CaseResult,
    CaseSkip,
    EvalReport,
    ModelTurn,
    ModelToolCall,
    NumericExpectation,
    OutputExpectation,
    TextExpectation,
    ToolContractCase,
    ToolCallExpectation,
    ToolLoopCase,
    TrajectoryCase,
)
from eval.sync_policyengine_uk import render_generated_cases
from eval.tool_loop_grading import (
    aggregate_tool_loop_trials,
    expectation_with_trace_numbers,
    numbers_from_value,
)
from tools.definitions import TOOL_DEFINITIONS


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_loads_yaml_cases_with_typed_schemas():
    cases = load_case_file(REPO_ROOT / "evals" / "cases" / "trajectory" / "core.yaml")

    assert cases
    assert {case.suite for case in cases} == {"trajectory"}
    assert cases[0].expected_tools[0].name == "run_household_simulation"


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


def test_grade_output_supports_chart_json_expectations():
    actual = {
        "status": "success",
        "chart_markdown": (
            "```chart\n"
            '{"type": "bar", "x": {"field": "decile"}, "data": [{"decile": 1, "change": 10}]}\n'
            "```"
        ),
    }
    expectation = OutputExpectation(
        contains={"status": "success"},
        chart_contains={
            "type": "bar",
            "x": {"field": "decile"},
            "data": [{"decile": 1}],
        },
    )

    assert grade_output(actual, expectation) == []


def test_grade_tool_calls_matches_ordered_semantic_expectations():
    actual = [
        ModelToolCall(name="validate_reform", input={"reform": {"income_tax": {"personal_allowance": 15000}}}),
        ModelToolCall(name="run_household_simulation", input={"year": 2025, "people": [], "benunit": {}, "household": {}}),
    ]
    expected = [
        ToolCallExpectation(
            name="validate_reform",
            input_contains={"reform": {"income_tax": {"personal_allowance": 15000}}},
        ),
        ToolCallExpectation(name="run_household_simulation", required_input_paths=["people"]),
    ]

    assert grade_tool_calls(actual, expected, forbidden_tools=["aggregate_result"]) == []


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
    expected_cases = (
        len(load_case_file(REPO_ROOT / "evals" / "cases" / "trajectory" / "core.yaml"))
        + len(load_case_file(REPO_ROOT / "evals" / "cases" / "answer" / "core.yaml"))
    )
    live_only_cases = (
        len(load_case_file(REPO_ROOT / "evals" / "cases" / "trajectory" / "live.yaml"))
        + len(load_case_file(REPO_ROOT / "evals" / "cases" / "answer" / "live.yaml"))
    )

    assert report.failed == 0
    assert report.passed + report.skipped == expected_cases + live_only_cases
    assert report.skipped >= live_only_cases


def test_offline_eval_runs_tool_loop_cases_without_reports():
    report = run_eval(
        suites=["tool_loop"],
        mode="offline",
        write_reports=False,
    )
    expected_cases = sum(
        len(load_case_file(path))
        for path in (REPO_ROOT / "evals" / "cases" / "tool_loop").glob("*.yaml")
    )

    assert report.failed == 0
    assert report.passed + report.skipped == expected_cases


def test_tool_loop_executes_tools_between_model_turns(tmp_path, monkeypatch):
    case_file = tmp_path / "tool_loop.yaml"
    case_file.write_text(
        yaml.safe_dump(
            {
                "cases": [
                    {
                        "id": "loop_case",
                        "suite": "tool_loop",
                        "description": "Tool loop executes a tool before grading final text.",
                        "prompt": "Calculate, then answer.",
                        "expected_tools": [
                            {
                                "name": "run_household_simulation",
                                "input_contains": {"year": 2025},
                            }
                        ],
                        "expect": {
                            "required": ["done", "£42"],
                            "grounded_numbers": True,
                        },
                        "offline_responses": [
                            {
                                "tool_calls": [
                                    {
                                        "name": "run_household_simulation",
                                        "input": {"year": 2025},
                                    }
                                ]
                            },
                            {"text": "done £42"},
                        ],
                    }
                ]
            },
            sort_keys=False,
        )
    )
    calls = []

    def record_tool_call(tool_name, tool_input, context=None):
        assert context is not None
        calls.append((tool_name, tool_input))
        return {"value": 42}

    monkeypatch.setattr(runner, "_case_paths", lambda _suites: [case_file])
    monkeypatch.setattr(runner, "execute_tool", record_tool_call)

    report = runner.run_eval(
        suites=["tool_loop"],
        mode="offline",
        write_reports=False,
    )

    assert report.failed == 0
    assert report.passed == 1
    assert calls == [("run_household_simulation", {"year": 2025})]


def test_tool_loop_cases_can_declare_live_trial_thresholds():
    case = ToolLoopCase(
        id="live_population_case",
        description="Live population evals run multiple trials.",
        prompt="What would this reform cost?",
        requirements=["live_model", "policyengine_py", "data"],
        expected_tools=[ToolCallExpectation(name="run_society_simulation")],
        trials=3,
        pass_threshold=0.66,
    )

    assert case.trials == 3
    assert case.pass_threshold == 0.66


def test_grounded_numbers_include_tool_inputs_outputs_and_display_scales():
    expectation = TextExpectation(grounded_numbers=True, allowed_numbers=[2026])
    tool_calls = [
        ModelToolCall(
            name="run_society_simulation",
            input={"year": 2026, "reform": {"basic_rate": 0.19}},
        )
    ]
    grounded = expectation_with_trace_numbers(
        expectation,
        tool_calls=tool_calls,
        tool_outputs=[{"budgetary_impact": -1_000_000_000}],
    )

    assert grade_text(
        "In 2026, a 19% rate has an annual impact of -£1bn.",
        grounded,
    ) == []


def test_number_collection_excludes_booleans_and_adds_percentage_variants():
    values = numbers_from_value(
        {"enabled": True, "rate": 0.2, "count": 5, "nested": [False, 2_000]}
    )

    assert 1.0 not in values
    assert 0.2 in values
    assert 20.0 in values
    assert 5.0 in values
    assert 2_000.0 in values
    assert 2.0 in values


def test_shared_trial_aggregation_applies_declared_threshold():
    case = ToolLoopCase(
        id="threshold_case",
        description="Threshold aggregation",
        prompt="Calculate",
        trials=3,
        pass_threshold=0.66,
    )
    trial_results = [
        CaseResult(id=case.id, suite=case.suite, status="passed", score=1.0),
        CaseResult(id=case.id, suite=case.suite, status="passed", score=1.0),
        CaseResult(
            id=case.id,
            suite=case.suite,
            status="failed",
            score=0.0,
            errors=["failed trial"],
        ),
    ]

    result = aggregate_tool_loop_trials(case, trial_results)

    assert result.status == "passed"
    assert result.score == pytest.approx(2 / 3)
    assert result.details["passed_trials"] == 2


def test_live_tool_loop_trials_score_against_threshold(tmp_path, monkeypatch):
    case_file = tmp_path / "tool_loop_live.yaml"
    case_file.write_text(
        yaml.safe_dump(
            {
                "cases": [
                    {
                        "id": "live_loop_case",
                        "suite": "tool_loop",
                        "description": "Live trials aggregate to a pass rate.",
                        "prompt": "Calculate, then answer.",
                        "trials": 3,
                        "pass_threshold": 0.66,
                        "expected_tools": [
                            {
                                "name": "run_household_simulation",
                                "input_contains": {"year": 2026},
                            }
                        ],
                        "expect": {"required": ["completed"]},
                    }
                ]
            },
            sort_keys=False,
        )
    )
    fake_client = runner.FakeModelClient(
        {
            "live_loop_case": [
                ModelTurn(tool_calls=[ModelToolCall(name="run_household_simulation", input={"year": 2026})]),
                ModelTurn(text="completed"),
                ModelTurn(tool_calls=[ModelToolCall(name="run_household_simulation", input={"year": 2026})]),
                ModelTurn(text="completed"),
                ModelTurn(tool_calls=[ModelToolCall(name="run_household_simulation", input={"year": 2026})]),
                ModelTurn(text="missing required text"),
            ]
        }
    )
    calls = []

    monkeypatch.setattr(runner, "_case_paths", lambda _suites: [case_file])
    monkeypatch.setattr(runner, "AnthropicModelClient", lambda model=None: fake_client)
    monkeypatch.setattr(
        runner,
        "execute_tool",
        lambda tool_name, tool_input, context=None: calls.append((tool_name, tool_input)) or {"status": "success"},
    )

    report = runner.run_eval(
        suites=["tool_loop"],
        mode="live",
        provider="anthropic",
        write_reports=False,
    )

    assert report.failed == 0
    assert report.passed == 1
    result = report.results[0]
    assert result.score == pytest.approx(2 / 3)
    assert result.details["passed_trials"] == 2
    assert result.details["failed_trials"] == 1
    assert len(result.details["trials"]) == 3
    assert calls == [
        ("run_household_simulation", {"year": 2026}),
        ("run_household_simulation", {"year": 2026}),
        ("run_household_simulation", {"year": 2026}),
    ]


def test_issue_229_live_population_cases_are_manual_live_tool_loops():
    cases = load_case_file(REPO_ROOT / "evals" / "cases" / "tool_loop" / "uk_population_live.yaml")

    assert len(cases) == 20
    assert all(isinstance(case, ToolLoopCase) for case in cases)
    assert all("live_model" in case.requirements for case in cases)
    assert all("policyengine_py" in case.requirements for case in cases)
    assert all("data" in case.requirements for case in cases)
    assert all(case.trials >= 3 for case in cases)
    assert all(case.pass_threshold >= 0.66 for case in cases)
    assert all(any(tool.name == "run_society_simulation" for tool in case.expected_tools) for case in cases)


def test_charts_mode_trajectory_adds_directive_and_keeps_tools():
    class RecordingClient:
        def __init__(self):
            self.calls = []

        def generate(self, **kwargs):
            self.calls.append(kwargs)
            return ModelTurn(tool_calls=[ModelToolCall(name="generate_chart", input={})])

    case = TrajectoryCase(
        id="charts_case",
        description="Charts mode keeps tools available.",
        prompt="Chart supplied data.",
        charts_mode=True,
        expected_tools=[ToolCallExpectation(name="generate_chart")],
    )
    client = RecordingClient()

    result = runner._run_trajectory(case, client)

    assert result.status == "passed"
    assert client.calls[0]["tools"]
    assert "chart mode" in client.calls[0]["system"]


def test_multiturn_trajectory_uses_case_messages():
    class RecordingClient:
        def __init__(self):
            self.calls = []

        def generate(self, **kwargs):
            self.calls.append(kwargs)
            return ModelTurn(text="ok")

    messages = [
        {"role": "user", "content": "I have dependants."},
        {"role": "assistant", "content": "What ages?"},
        {"role": "user", "content": "4 and 9."},
    ]
    case = TrajectoryCase(
        id="multi_turn_case",
        description="Uses supplied transcript.",
        prompt="fallback prompt",
        messages=messages,
    )
    client = RecordingClient()

    result = runner._run_trajectory(case, client)

    assert result.status == "passed"
    assert client.calls[0]["messages"] == messages


def test_report_markdown_exposes_counts_and_case_status_for_regression_review():
    report = EvalReport(
        mode="live",
        suites=["trajectory"],
        provider="anthropic",
        model="claude-haiku-4-5",
        git_sha="abc123",
        started_at="2026-06-02T00:00:00+00:00",
        finished_at="2026-06-02T00:00:01+00:00",
        results=[
            CaseResult(id="passed_case", suite="trajectory", status="passed", score=1.0),
            CaseResult(
                id="failed_case",
                suite="trajectory",
                status="failed",
                score=0.0,
                errors=["expected tool 'run_household_simulation'"],
            ),
        ],
    )

    markdown = render_markdown(report)

    assert "Provider: `anthropic`" in markdown
    assert "Model: `claude-haiku-4-5`" in markdown
    assert "Passed: `1`" in markdown
    assert "Failed: `1`" in markdown
    assert "`failed_case`" in markdown
    assert "expected tool" in markdown


def test_report_markdown_serializes_policyengine_date_keys():
    report = EvalReport(
        mode="offline",
        suites=["tool_contract"],
        provider="offline",
        started_at="2026-07-21T00:00:00+00:00",
        finished_at="2026-07-21T00:00:01+00:00",
        results=[
            CaseResult(
                id="dated_reform",
                suite="tool_contract",
                status="passed",
                score=1.0,
                details={"actual": {"reform_object": {date(2026, 1, 1): 15_000}}},
            )
        ],
    )

    markdown = render_markdown(report)

    assert '"2026-01-01": 15000' in markdown


def test_skipped_tool_contract_cases_require_source_metadata():
    with pytest.raises(ValidationError):
        ToolContractCase(
            id="missing_source",
            description="Skipped cases need traceable upstream source metadata.",
            tool_name="run_household_simulation",
            input={},
            skip=CaseSkip(
                code="policyengine_py_coverage_gap",
                reason="policyengine_py gap",
                remove_when="policyengine_py supports this case",
            ),
        )


def test_skipped_tool_contract_case_reports_skip_without_execution(tmp_path, monkeypatch):
    case_file = tmp_path / "cases.yaml"
    case_file.write_text(
        yaml.safe_dump(
            {
                "cases": [
                    {
                        "id": "py_gap_case",
                        "suite": "tool_contract",
                        "description": "Skipped upstream case.",
                        "source": {
                            "package": "policyengine-uk",
                            "version": "1.0.0",
                            "path": "tests/policy/example.yaml",
                            "name": "Skipped case",
                        },
                        "skip": {
                            "code": "policyengine_py_coverage_gap",
                            "reason": "policyengine_py does not expose this behavior",
                            "remove_when": "policyengine_py exposes this behavior",
                        },
                        "tool_name": "run_household_simulation",
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
    assert "policyengine_py_coverage_gap" in report.results[0].errors[0]


def test_policyengine_uk_generated_cases_validate():
    cases = load_case_file(REPO_ROOT / "evals" / "cases" / "tool_contract" / "policyengine_uk.generated.yaml")

    assert len(cases) >= 4
    assert any(isinstance(case, ToolContractCase) and case.skip for case in cases)
    assert all(case.source for case in cases if case.id.startswith("policyengine_uk_"))


def test_all_eval_tool_calls_match_current_tool_schemas():
    schemas = {tool["name"]: tool["input_schema"] for tool in TOOL_DEFINITIONS}
    errors = []

    for suite, case_dir in runner.SUITE_DIRS.items():
        for path in sorted(case_dir.glob("*.yaml")):
            for case in load_case_file(path):
                calls = []
                if isinstance(case, ToolContractCase):
                    if "schema_negative" in case.tags:
                        continue
                    calls = [(case.tool_name, case.input)]
                elif isinstance(case, AnswerCase):
                    calls = [(call.name, call.input) for call in case.tool_calls]
                elif isinstance(case, TrajectoryCase) and case.offline_response:
                    calls = [
                        (call.name, call.input)
                        for call in case.offline_response.tool_calls
                    ]
                elif isinstance(case, ToolLoopCase):
                    calls = [
                        (call.name, call.input)
                        for turn in case.offline_responses
                        for call in turn.tool_calls
                    ]

                expected_calls = getattr(case, "expected_tools", [])
                for expected_call in expected_calls:
                    schema = schemas.get(expected_call.name)
                    if schema is None:
                        errors.append(
                            f"{path.name}:{case.id}: unknown expected tool "
                            f"{expected_call.name}"
                        )
                        continue
                    partial_schema = deepcopy(schema)
                    partial_schema["required"] = []
                    for error in Draft202012Validator(partial_schema).iter_errors(
                        expected_call.input_contains
                    ):
                        location = ".".join(
                            str(part) for part in error.absolute_path
                        )
                        errors.append(
                            f"{path.name}:{case.id}:{expected_call.name}:"
                            f"{location}: {error.message}"
                        )

                for tool_name, tool_input in calls:
                    schema = schemas.get(tool_name)
                    if schema is None:
                        errors.append(f"{path.name}:{case.id}: unknown tool {tool_name}")
                        continue
                    for error in Draft202012Validator(schema).iter_errors(tool_input):
                        location = ".".join(str(part) for part in error.absolute_path)
                        errors.append(
                            f"{path.name}:{case.id}:{tool_name}:{location}: {error.message}"
                        )

    assert errors == []


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
                "runtime_distribution": "policyengine",
                "cases": [
                    {
                        "id": "active_case",
                        "path": "tests/policy/sample.yaml",
                        "name": "Active case",
                        "output_map": {"income_tax": "person[].income_tax"},
                    },
                    {
                        "id": "skipped_case",
                        "path": "tests/policy/sample.yaml",
                        "name": "Skipped case",
                        "skip": {
                            "code": "policyengine_py_coverage_gap",
                            "reason": "policyengine_py gap",
                            "remove_when": "policyengine_py supports it",
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
        runtime_version="4.5.6",
        variable_entities={"employment_income": "person"},
    )
    generated = yaml.safe_load(rendered)

    assert generated["source"]["version"] == "1.2.3"
    assert generated["source"]["runtime_version"] == "4.5.6"
    assert generated["cases"][0]["input"]["people"][0]["employment_income"] == 10000
    assert generated["cases"][0]["input"]["extra_variables"] == ["income_tax"]
    assert generated["cases"][0]["expect"]["numeric"][0]["path"] == "person[].income_tax"
    assert generated["cases"][0]["expect"]["numeric"][0]["tolerance"] == 2.0
    assert generated["cases"][1]["skip"]["code"] == "policyengine_py_coverage_gap"


def test_policyengine_uk_sync_routes_entities_parameters_and_arithmetic(tmp_path):
    package_root = tmp_path / "policyengine_uk"
    policy_dir = package_root / "tests" / "policy"
    policy_dir.mkdir(parents=True)
    (policy_dir / "sample.yaml").write_text(
        yaml.safe_dump(
            [
                {
                    "name": "Entity routing",
                    "period": 2026,
                    "input": {
                        "employment_income": 30_000,
                        "would_claim_uc": True,
                        "savings": 500,
                        "gov.example.rate": 0.2,
                    },
                    "output": {"income_tax": "(30_000 - 12_500) * 0.2"},
                }
            ],
            sort_keys=False,
        )
    )
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "cases": [
                    {
                        "id": "entity_routing",
                        "path": "tests/policy/sample.yaml",
                        "name": "Entity routing",
                        "output_map": {"income_tax": "person[].income_tax"},
                    }
                ]
            },
            sort_keys=False,
        )
    )

    rendered = render_generated_cases(
        manifest,
        package_root=package_root,
        package_version="1.2.3",
        runtime_version="4.5.6",
        variable_entities={
            "employment_income": "person",
            "would_claim_uc": "benunit",
            "savings": "household",
        },
    )
    generated_input = yaml.safe_load(rendered)["cases"][0]["input"]
    expectation = yaml.safe_load(rendered)["cases"][0]["expect"]["numeric"][0]

    assert generated_input["people"] == [{"employment_income": 30_000}]
    assert generated_input["benunit"] == {"would_claim_uc": True}
    assert generated_input["household"] == {"savings": 500}
    assert generated_input["reform"] == {"gov.example.rate": 0.2}
    assert expectation["path"] == "reform.person[].income_tax"
    assert expectation["equals"] == 3_500
