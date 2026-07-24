import sys
from types import ModuleType, SimpleNamespace

import pytest

from eval import providers
from eval import reporting
from eval import run
from eval.schemas import CaseResult, EvalReport, ModelTurn


def _report(*, failed=0):
    results = [
        CaseResult(
            id=f"case-{index}",
            suite="trajectory",
            status="failed" if index < failed else "passed",
            score=0.0 if index < failed else 1.0,
        )
        for index in range(max(1, failed))
    ]
    return EvalReport(
        mode="offline",
        suites=["trajectory"],
        provider="offline",
        started_at="2026-07-21T12:00:00+00:00",
        finished_at="2026-07-21T12:00:01+00:00",
        results=results,
    )


def test_fake_model_client_supports_single_and_sequential_turns():
    single = ModelTurn(text="single")
    client = providers.FakeModelClient(
        {
            "single": single,
            "sequence": [ModelTurn(text="first"), ModelTurn(text="second")],
        }
    )

    assert client.generate(case_id="single", messages=[], system="") is single
    assert client.generate(case_id="sequence", messages=[], system="").text == "first"
    assert client.generate(case_id="sequence", messages=[], system="").text == "second"

    with pytest.raises(ValueError, match="turn 3"):
        client.generate(case_id="sequence", messages=[], system="")
    with pytest.raises(ValueError, match="missing"):
        client.generate(case_id="missing", messages=[], system="")


def test_anthropic_client_requires_api_key(monkeypatch):
    anthropic = ModuleType("anthropic")
    anthropic.Anthropic = lambda **_kwargs: object()
    monkeypatch.setitem(sys.modules, "anthropic", anthropic)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        providers.AnthropicModelClient()


def test_anthropic_client_translates_text_and_tool_blocks(monkeypatch):
    calls = []
    response = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text="Result: "),
            SimpleNamespace(
                type="tool_use", id="tool-1", name="validate_reform", input={"x": 1}
            ),
            SimpleNamespace(
                type="tool_use", id="tool-2", name="ignored_input", input="not a dict"
            ),
            SimpleNamespace(type="other"),
            SimpleNamespace(type="text", text="done"),
        ]
    )

    class Anthropic:
        def __init__(self, **kwargs):
            assert kwargs == {"api_key": "test-key"}
            self.messages = SimpleNamespace(
                create=lambda **create_kwargs: calls.append(create_kwargs) or response
            )

    anthropic = ModuleType("anthropic")
    anthropic.Anthropic = Anthropic
    monkeypatch.setitem(sys.modules, "anthropic", anthropic)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    client = providers.AnthropicModelClient(
        model="test-model",
        max_tokens=123,
    )
    turn = client.generate(
        case_id="case-1",
        messages=[{"role": "user", "content": "hello"}],
        system="system",
        tools=[{"name": "validate_reform"}],
    )

    assert turn.text == "Result: done"
    assert [call.model_dump() for call in turn.tool_calls] == [
        {"id": "tool-1", "name": "validate_reform", "input": {"x": 1}},
        {"id": "tool-2", "name": "ignored_input", "input": {}},
    ]
    assert calls[0]["model"] == "test-model"
    assert calls[0]["max_tokens"] == 123
    assert calls[0]["tools"] == [{"name": "validate_reform"}]


def test_anthropic_client_omits_empty_tools(monkeypatch):
    client = object.__new__(providers.AnthropicModelClient)
    calls = []
    client.client = SimpleNamespace(
        messages=SimpleNamespace(
            create=lambda **kwargs: calls.append(kwargs) or SimpleNamespace(content=[])
        )
    )
    client.model_override = "test-model"
    client.max_tokens = 50

    assert client.generate(case_id="case", messages=[], system="", tools=[]).text == ""
    assert "tools" not in calls[0]


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (
            ["eval"],
            {
                "suite": None,
                "mode": "offline",
                "provider": None,
                "trials": 1,
                "model_cases_only": False,
                "strict_requirements": False,
            },
        ),
        (
            [
                "eval",
                "--suite",
                "trajectory",
                "--suite",
                "answer",
                "--mode",
                "live",
                "--provider",
                "anthropic",
                "--trials",
                "3",
                "--model-cases-only",
                "--strict-requirements",
                "--case",
                "case-1",
                "--tag",
                "critical",
            ],
            {
                "suite": ["trajectory", "answer"],
                "mode": "live",
                "provider": "anthropic",
                "trials": 3,
                "model_cases_only": True,
                "strict_requirements": True,
                "case": ["case-1"],
                "tag": ["critical"],
            },
        ),
    ],
)
def test_eval_cli_parses_defaults_and_repeated_suites(monkeypatch, argv, expected):
    monkeypatch.setattr(sys, "argv", argv)
    args = run.parse_args()
    assert {key: getattr(args, key) for key in expected} == expected


@pytest.mark.parametrize(("suites", "failed", "exit_code"), [(None, 0, 0), (["all"], 1, 1)])
def test_eval_main_expands_all_suites_and_returns_failure_status(
    monkeypatch, capsys, suites, failed, exit_code
):
    calls = []
    monkeypatch.setattr(
        run,
        "parse_args",
        lambda: SimpleNamespace(
            suite=suites,
            mode="offline",
                provider=None,
                model=None,
                trials=1,
                case=None,
                tag=None,
                model_cases_only=False,
                strict_requirements=False,
                report_dir=None,
            no_report=False,
        ),
    )
    monkeypatch.setattr(
        run,
        "run_eval",
        lambda **kwargs: calls.append(kwargs) or _report(failed=failed),
    )

    assert run.main() == exit_code
    assert calls[0]["suites"] == list(run.SUITE_DIRS)
    assert "AI evals:" in capsys.readouterr().out


def test_eval_main_preserves_selected_suites_and_no_report(monkeypatch):
    calls = []
    monkeypatch.setattr(
        run,
        "parse_args",
        lambda: SimpleNamespace(
            suite=["answer"],
            mode="live",
            provider="anthropic",
            model="test-model",
            trials=3,
            case=["one"],
            tag=["critical"],
            model_cases_only=True,
            strict_requirements=True,
            report_dir=None,
            no_report=True,
        ),
    )
    monkeypatch.setattr(
        run,
        "run_eval",
        lambda **kwargs: calls.append(kwargs) or _report(),
    )

    assert run.main() == 0
    assert calls[0]["suites"] == ["answer"]
    assert calls[0]["write_reports"] is False
    assert calls[0]["trials"] == 3
    assert calls[0]["case_ids"] == ["one"]
    assert calls[0]["tags"] == ["critical"]
    assert calls[0]["model_cases_only"] is True
    assert calls[0]["strict_requirements"] is True


def test_write_report_creates_json_and_markdown_files(tmp_path):
    report = _report()

    json_path, markdown_path = reporting.write_report(report, tmp_path / "reports")

    assert json_path.name == "20260721T1200000000-offline.json"
    assert json_path.exists()
    assert '"provider": "offline"' in json_path.read_text()
    assert markdown_path.exists()
    assert "# UK Chat AI Eval Report" in markdown_path.read_text()
