from copy import deepcopy
from datetime import date
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator
from pydantic import ValidationError

import eval.runner as runner
from eval.graders import (
    grade_live_text,
    grade_output,
    grade_text,
    grade_tool_calls,
    grade_tool_results,
)
from eval.loaders import load_case_file
from eval.reporting import render_markdown
from eval.runner import run_eval
from eval.schemas import (
    AnswerCase,
    CaseResult,
    CaseSkip,
    EvalReport,
    ExecutedToolResult,
    LiveAnswerCase,
    LiveTextExpectation,
    ModelTurn,
    ModelToolCall,
    NumericExpectation,
    OutputExpectation,
    RequiredAnswerValue,
    TextExpectation,
    ToolContractCase,
    ToolContractDetails,
    ToolCallExpectation,
    ToolLoopCase,
    ToolResultExpectation,
    TrajectoryCase,
)
from eval.sync_policyengine_uk import render_generated_cases
from tools.definitions import TOOL_DEFINITIONS


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_loads_yaml_cases_with_typed_schemas():
    cases = load_case_file(REPO_ROOT / "evals" / "cases" / "trajectory" / "core.yaml")

    assert cases
    assert {case.suite for case in cases} == {"trajectory"}
    assert cases[0].expected_tools[0].name == "run_household_simulation"


def test_deterministic_and_live_case_roots_are_disjoint():
    deterministic_cases = [
        case
        for path in runner._case_paths(runner.DETERMINISTIC_SUITE_DIRS)
        for case in load_case_file(path)
    ]
    live_cases = [
        case
        for path in runner._case_paths(runner.LIVE_SUITE_DIRS, live=True)
        for case in load_case_file(path, live=True)
    ]

    deterministic_ids = {case.id for case in deterministic_cases}
    live_ids = {case.id for case in live_cases}

    assert deterministic_ids.isdisjoint(live_ids)
    assert all("live_model" not in case.requirements for case in deterministic_cases)
    assert all("live_model" in case.requirements for case in live_cases)


def test_deterministic_loader_rejects_live_model_cases(tmp_path):
    path = tmp_path / "trajectory.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "cases": [
                    {
                        "id": "misplaced_live_case",
                        "suite": "trajectory",
                        "description": "Live cases do not belong in deterministic roots.",
                        "requirements": ["live_model"],
                        "prompt": "Calculate.",
                        "offline_response": {"text": "scripted"},
                    }
                ]
            }
        )
    )

    with pytest.raises(ValueError, match="cannot require a live model"):
        load_case_file(path)


def test_deterministic_loader_requires_scripted_model_response(tmp_path):
    path = tmp_path / "answer.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "cases": [
                    {
                        "id": "unscripted_deterministic_case",
                        "suite": "answer",
                        "description": "Deterministic cases need authored output.",
                        "prompt": "Calculate.",
                    }
                ]
            }
        )
    )

    with pytest.raises(ValueError, match="require offline_response"):
        load_case_file(path)


def test_live_loader_rejects_scripted_model_responses(tmp_path):
    path = tmp_path / "trajectory.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "cases": [
                    {
                        "id": "misplaced_scripted_case",
                        "suite": "trajectory",
                        "description": "Scripted responses do not belong in live roots.",
                        "requirements": ["live_model"],
                        "prompt": "Calculate.",
                        "offline_response": {"text": "scripted"},
                    }
                ]
            }
        )
    )

    with pytest.raises(ValidationError):
        load_case_file(path, live=True)


@pytest.mark.parametrize(
    ("mode", "suite", "layer"),
    [
        ("offline", "gateway", "deterministic"),
        ("live", "tool_contract", "live"),
    ],
)
def test_runner_rejects_suites_from_the_other_eval_layer(mode, suite, layer):
    with pytest.raises(ValueError, match=f"not a {layer} eval suite"):
        run_eval(
            suites=[suite],
            mode=mode,
            write_reports=False,
        )


def test_yaml_loader_normalizes_dated_reform_keys_to_json_strings():
    cases = load_case_file(
        REPO_ROOT / "evals" / "cases" / "tool_contract" / "reforms.yaml"
    )
    case = next(
        case
        for case in cases
        if case.id == "validate_reform_accepts_dated_parameter"
    )

    assert (
        case.input["reform"][
            "gov.hmrc.income_tax.allowances.personal_allowance.amount"
        ]
        == {"2026-01-01": 15_000}
    )


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


def test_grade_tool_calls_allows_additional_calls_around_ordered_expectations():
    actual = [
        ModelToolCall(name="search_parameters"),
        ModelToolCall(name="run_household_simulation"),
        ModelToolCall(name="generate_chart"),
    ]
    expected = [ToolCallExpectation(name="run_household_simulation")]

    errors = grade_tool_calls(actual, expected, forbidden_tools=[])

    assert errors == []


def test_grade_tool_results_supports_ranges_and_last_successful_retry():
    results = [
        ExecutedToolResult(
            name="compute_budgetary_impact",
            output={"error": "temporary failure"},
        ),
        ExecutedToolResult(
            name="compute_budgetary_impact",
            output={
                "status": "success",
                "net_budgetary_impact": 18_667_418_015,
            },
        ),
    ]
    expectations = [
        ToolResultExpectation(
            tool_name="compute_budgetary_impact",
            result_selection="last_successful",
            expect=OutputExpectation(
                contains={"status": "success"},
                numeric=[
                    NumericExpectation(
                        path="net_budgetary_impact",
                        min=14_000_000_000,
                        max=25_000_000_000,
                    )
                ],
            ),
        )
    ]

    assert grade_tool_results(results, expectations) == []

    expectations[0].expect.numeric[0].max = 10_000_000_000
    assert "expected <= 10000000000" in grade_tool_results(
        results,
        expectations,
    )[0]


def test_grade_text_checks_required_forbidden_and_grounded_numbers():
    expectation = TextExpectation(
        required=["illustrative", "£200"],
        forbidden=["fair", "generous"],
        grounded_numbers=True,
        allowed_numbers=[200, 2025],
    )

    assert grade_text("In 2025, this illustrative household changes by £200.", expectation) == []


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("The policy is progressive.", "progressive"),
        ("The impact rises progressively through the deciles.", "progressive"),
        ("This changes tax progressivity.", "progressive"),
        ("The policy is regressive.", "regressive"),
        ("The impact falls regressively through the deciles.", "regressive"),
        ("This changes tax regressivity.", "regressive"),
    ],
)
def test_factual_neutrality_rejects_intended_word_families(text, label):
    errors = grade_text(text, TextExpectation(factual_neutrality=True))

    assert errors == [f"factual neutrality label present: {label!r}"]


@pytest.mark.parametrize(
    "text",
    [
        "The average reduction increases from decile 1 to decile 10.",
        "The analysis shows progression across the income distribution.",
        "A nonprogressive identifier is mentioned as code.",
    ],
)
def test_factual_neutrality_allows_measured_or_unrelated_language(text):
    assert grade_text(text, TextExpectation(factual_neutrality=True)) == []


def test_grade_text_requires_values_from_named_tool_results():
    expectation = LiveTextExpectation(
        grounded_numbers=True,
        required_values=[
            RequiredAnswerValue(
                tool_name="run_household_simulation",
                path="household.0.baseline_household_net_income",
                tolerance=0.01,
                required_context=["net income"],
            )
        ],
    )
    results = [
        ExecutedToolResult(
            name="run_household_simulation",
            output={"household": [{"baseline_household_net_income": 31_234.56}]},
        )
    ]

    assert (
        grade_live_text(
            "Baseline net income is £31,234.56.",
            expectation,
            results,
        )
        == []
    )
    assert "omitted required value" in grade_live_text(
        "Baseline net income is unavailable.",
        expectation,
        results,
    )[0]


def test_grade_text_supports_scaled_required_percentages():
    expectation = LiveTextExpectation(
        grounded_numbers=True,
        required_values=[
            RequiredAnswerValue(
                tool_name="get_parameter",
                path="parameter.value",
                scale=100,
                required_context=["rate"],
            )
        ],
    )
    results = [
        ExecutedToolResult(
            name="get_parameter",
            output={"parameter": {"value": 0.2}},
        )
    ]

    assert grade_live_text("The rate is 20%.", expectation, results) == []


def test_required_value_tolerance_also_applies_to_grounding():
    expectation = LiveTextExpectation(
        grounded_numbers=True,
        required_values=[
            RequiredAnswerValue(
                tool_name="run_household_simulation",
                path="household.household_net_income",
                tolerance=1,
            )
        ],
    )
    results = [
        ExecutedToolResult(
            name="run_household_simulation",
            output={"household": {"household_net_income": 28_539.55}},
        )
    ]

    assert grade_live_text("Net income is £28,540.", expectation, results) == []


def test_live_grounding_accepts_tool_inputs_and_declared_derivations():
    expectation = LiveTextExpectation(
        grounded_numbers=True,
        allowed_derived_numbers=[81.5],
    )
    results = [
        ExecutedToolResult(
            name="run_household_simulation",
            input={"people": [{"age": 35, "employment_income": 35_000}]},
            output={"household": {"household_net_income": 28_539.55}},
        )
    ]

    assert (
        grade_live_text(
            "Age 35, income £35,000, net income £28,539.55, retained 81.5%.",
            expectation,
            results,
        )
        == []
    )


def test_live_text_accepts_any_authored_semantic_alternative():
    expectation = LiveTextExpectation(
        required_any=[
            [
                "illustrative",
                "simulation",
                "synthetic",
            ]
        ]
    )

    assert grade_live_text("Based on the simulation result.", expectation) == []
    assert "missing required alternative" in grade_live_text(
        "Here is the result.",
        expectation,
    )[0]


def test_live_required_value_can_select_last_successful_retry():
    expectation = LiveTextExpectation(
        required_values=[
            RequiredAnswerValue(
                tool_name="run_household_simulation",
                path="household.household_net_income",
                result_selection="last_successful",
                tolerance=1,
            )
        ],
    )
    results = [
        ExecutedToolResult(
            name="run_household_simulation",
            output={"error": "invalid input"},
        ),
        ExecutedToolResult(
            name="run_household_simulation",
            output={
                "status": "success",
                "household": {"household_net_income": 28_539.55},
            },
        ),
    ]

    assert grade_live_text("Net income is £28,540.", expectation, results) == []


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
    assert report.failed == 0
    assert report.passed == expected_cases
    assert report.skipped == 0


def test_offline_eval_runs_tool_loop_cases_without_reports():
    report = run_eval(
        suites=["tool_loop"],
        mode="offline",
        write_reports=False,
    )
    expected_cases = len(load_case_file(REPO_ROOT / "evals" / "cases" / "tool_loop" / "core.yaml"))

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
                        "expected_tool_results": [
                            {
                                "tool_name": "run_household_simulation",
                                "expect": {
                                    "contains": {"status": "success"},
                                    "numeric": [
                                        {
                                            "path": "value",
                                            "min": 40,
                                            "max": 45,
                                        }
                                    ],
                                },
                            }
                        ],
                        "expect": {
                            "required": ["done", "£42"],
                            "grounded_numbers": True,
                            "allowed_numbers": [42],
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
        return {"status": "success", "value": 42}

    monkeypatch.setattr(
        runner,
        "_case_paths",
        lambda _suites, live=False: [case_file],
    )
    monkeypatch.setattr(runner, "execute_tool", record_tool_call)

    report = runner.run_eval(
        suites=["tool_loop"],
        mode="offline",
        write_reports=False,
    )

    assert report.failed == 0
    assert report.passed == 1
    assert calls == [("run_household_simulation", {"year": 2025})]


def test_tool_loop_reports_tool_execution_errors(tmp_path, monkeypatch):
    case_file = tmp_path / "tool_loop.yaml"
    case_file.write_text(
        yaml.safe_dump(
            {
                "cases": [
                    {
                        "id": "failing_loop",
                        "suite": "tool_loop",
                        "description": "Tool errors become case failures.",
                        "prompt": "Calculate.",
                        "expected_tools": [
                            {"name": "run_household_simulation"}
                        ],
                        "offline_responses": [
                            {
                                "tool_calls": [
                                    {
                                        "name": "run_household_simulation",
                                        "input": {},
                                    }
                                ]
                            }
                        ],
                    }
                ]
            },
            sort_keys=False,
        )
    )

    def fail_tool(*_args, **_kwargs):
        raise RuntimeError("calculation failed")

    monkeypatch.setattr(
        runner,
        "_case_paths",
        lambda _suites, live=False: [case_file],
    )
    monkeypatch.setattr(runner, "execute_tool", fail_tool)

    report = runner.run_eval(
        suites=["tool_loop"],
        mode="offline",
        write_reports=False,
    )

    assert report.failed == 1
    assert "RuntimeError: calculation failed" in report.results[0].errors[0]


def test_offline_tool_loop_resolves_prior_tool_results_and_fields(
    tmp_path,
    monkeypatch,
):
    case_file = tmp_path / "tool_loop.yaml"
    case_file.write_text(
        yaml.safe_dump(
            {
                "cases": [
                    {
                        "id": "stored_result_loop_case",
                        "suite": "tool_loop",
                        "description": "A stored simulation handle feeds retrieval.",
                        "prompt": "Run a simulation, then retrieve its impact.",
                        "expected_tools": [
                            {"name": "run_society_simulation"},
                            {
                                "name": "compute_budgetary_impact",
                                "input_contains": {
                                    "simulation_id": "society_simulation_test"
                                },
                            },
                            {
                                "name": "generate_chart",
                                "input_contains": {
                                    "data": {
                                        "net_budgetary_impact": 42,
                                    }
                                },
                            },
                        ],
                        "expect": {"required": ["done"]},
                        "offline_responses": [
                            {
                                "tool_calls": [
                                    {
                                        "name": "run_society_simulation",
                                        "input": {},
                                    }
                                ]
                            },
                            {
                                "tool_calls": [
                                    {
                                        "name": "compute_budgetary_impact",
                                        "input": {
                                            "simulation_id": (
                                                "$tool_result."
                                                "run_society_simulation.result_id"
                                            )
                                        },
                                    }
                                ]
                            },
                            {
                                "tool_calls": [
                                    {
                                        "name": "generate_chart",
                                        "input": {
                                            "chart_kind": "generic_line",
                                            "data": {
                                                "$tool_result": (
                                                    "compute_budgetary_impact"
                                                )
                                            },
                                        },
                                    }
                                ]
                            },
                            {"text": "done"},
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
        if tool_name == "run_society_simulation":
            return {"result_id": "society_simulation_test"}
        if tool_name == "generate_chart":
            return {"status": "success"}
        return {"net_budgetary_impact": 42}

    monkeypatch.setattr(
        runner,
        "_case_paths",
        lambda _suites, live=False: [case_file],
    )
    monkeypatch.setattr(runner, "execute_tool", record_tool_call)

    report = runner.run_eval(
        suites=["tool_loop"],
        mode="offline",
        write_reports=False,
    )

    assert report.failed == 0
    assert report.passed == 1
    assert calls == [
        ("run_society_simulation", {}),
        (
            "compute_budgetary_impact",
            {"simulation_id": "society_simulation_test"},
        ),
        (
            "generate_chart",
            {
                "chart_kind": "generic_line",
                "data": {"net_budgetary_impact": 42},
            },
        ),
    ]


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


def test_report_computes_pass_at_1_and_pass_all_trials_for_live_cases():
    results = [
        CaseResult(
            id="stable",
            suite="trajectory",
            trial=trial,
            status="passed",
            score=1.0,
        )
        for trial in range(1, 4)
    ]
    results.extend(
        CaseResult(
            id="flaky",
            suite="trajectory",
            trial=trial,
            status="passed" if trial == 1 else "failed",
            score=1.0 if trial == 1 else 0.0,
        )
        for trial in range(1, 4)
    )
    results.append(
        CaseResult(
            id="deterministic",
            suite="tool_contract",
            status="passed",
            score=1.0,
        )
    )
    report = EvalReport(
        mode="live",
        suites=["trajectory", "tool_contract"],
        provider="anthropic",
        started_at="2026-07-21T00:00:00+00:00",
        finished_at="2026-07-21T00:00:01+00:00",
        results=results,
    )

    assert report.trial_count == 3
    assert report.pass_at_1 == 1.0
    assert report.pass_all_trials == 0.5
    assert report.model_dump()["pass_all_trials"] == 0.5
    assert "Model pass^3: `50.0%`" in render_markdown(report)


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
                details=ToolContractDetails(
                    output=runner._json_object(
                        {"reform_object": {date(2026, 1, 1): 15_000}}
                    )
                ),
            )
        ],
    )

    markdown = render_markdown(report)

    assert '"2026-01-01": 15000' in markdown


def test_live_eval_runs_three_trials_through_production_model_routing(
    tmp_path,
    monkeypatch,
):
    case_file = tmp_path / "trajectory.yaml"
    case_file.write_text(
        yaml.safe_dump(
            {
                "cases": [
                    {
                        "id": "routed_case",
                        "suite": "trajectory",
                        "description": "A live case uses production routing.",
                        "requirements": ["live_model"],
                        "prompt": "Calculate this household.",
                        "expected_tools": [
                            {"name": "run_household_simulation"}
                        ],
                    }
                ]
            },
            sort_keys=False,
        )
    )
    calls = []

    class RecordingLiveClient:
        def __init__(self, model=None):
            self.model_override = model

        def generate(self, **kwargs):
            calls.append(kwargs)
            return ModelTurn(
                tool_calls=[
                    ModelToolCall(name="run_household_simulation")
                ]
            )

    monkeypatch.setattr(
        runner,
        "_case_paths",
        lambda _suites, live=False: [case_file],
    )
    monkeypatch.setattr(runner, "AnthropicModelClient", RecordingLiveClient)
    monkeypatch.setattr(
        runner,
        "select_chat_model",
        lambda _messages, charts_mode=False: "production-model",
    )

    report = runner.run_eval(
        suites=["trajectory"],
        mode="live",
        trials=3,
        strict_requirements=True,
        write_reports=False,
    )

    assert report.failed == 0
    assert [result.trial for result in report.results] == [1, 2, 3]
    assert {result.model for result in report.results} == {"production-model"}
    assert [call["model"] for call in calls] == ["production-model"] * 3
    assert report.pass_at_1 == 1.0
    assert report.pass_all_trials == 1.0


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

    monkeypatch.setattr(
        runner,
        "_case_paths",
        lambda _suites, live=False: [case_file],
    )
    monkeypatch.setattr(runner, "execute_tool", fail_if_called)

    report = runner.run_eval(
        suites=["tool_contract"],
        mode="offline",
        write_reports=False,
    )

    assert report.skipped == 1
    assert report.failed == 0
    assert "policyengine_py_coverage_gap" in report.results[0].errors[0]


def test_strict_requirements_turns_unavailable_cases_into_failures(
    tmp_path,
    monkeypatch,
):
    case_file = tmp_path / "cases.yaml"
    case_file.write_text(
        yaml.safe_dump(
            {
                "cases": [
                    {
                        "id": "data_case",
                        "suite": "tool_contract",
                        "description": "Data is mandatory in strict mode.",
                        "requirements": ["data"],
                        "tool_name": "run_society_simulation",
                        "input": {},
                    }
                ]
            }
        )
    )
    monkeypatch.delenv("RUN_DATA_EVALS", raising=False)
    monkeypatch.setattr(
        runner,
        "_case_paths",
        lambda _suites, live=False: [case_file],
    )

    report = runner.run_eval(
        suites=["tool_contract"],
        mode="offline",
        strict_requirements=True,
        write_reports=False,
    )

    assert report.failed == 1
    assert report.skipped == 0
    assert "RUN_DATA_EVALS" in report.results[0].errors[0]


def test_policyengine_uk_generated_cases_validate():
    cases = load_case_file(REPO_ROOT / "evals" / "cases" / "tool_contract" / "policyengine_uk.generated.yaml")

    assert len(cases) >= 4
    assert any(isinstance(case, ToolContractCase) and case.skip for case in cases)
    assert all(case.source for case in cases if case.id.startswith("policyengine_uk_"))


def test_all_eval_tool_calls_match_current_tool_schemas():
    schemas = {tool["name"]: tool["input_schema"] for tool in TOOL_DEFINITIONS}
    errors = []

    layers = [
        (runner.DETERMINISTIC_SUITE_DIRS, False),
        (runner.LIVE_SUITE_DIRS, True),
    ]
    for suite_dirs, live in layers:
        for case_dir in suite_dirs.values():
            for path in sorted(case_dir.glob("*.yaml")):
                for case in load_case_file(path, live=live):
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
                        for error in Draft202012Validator(
                            partial_schema
                        ).iter_errors(expected_call.input_contains):
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
                            errors.append(
                                f"{path.name}:{case.id}: unknown tool {tool_name}"
                            )
                            continue
                        for error in Draft202012Validator(schema).iter_errors(
                            tool_input
                        ):
                            location = ".".join(
                                str(part) for part in error.absolute_path
                            )
                            errors.append(
                                f"{path.name}:{case.id}:{tool_name}:{location}: "
                                f"{error.message}"
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
