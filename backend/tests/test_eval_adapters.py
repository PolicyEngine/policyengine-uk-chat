import json
import sys
from types import ModuleType, SimpleNamespace

import httpx
import pytest
from anthropic import Anthropic

from eval import providers
from eval import reporting
from eval import run
from eval import runner
from eval.schemas import (
    CaseResult,
    EvalReport,
    FrozenToolCall,
    ModelTurn,
    TextExpectation,
    ToolCallExpectation,
    ToolLoopCase,
)


def _block(**fields):
    return SimpleNamespace(**fields, model_dump=lambda **_kwargs: fields)


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


def test_anthropic_client_defaults_to_sonnet_with_thinking_headroom(monkeypatch):
    calls = []
    anthropic = ModuleType("anthropic")
    anthropic.Anthropic = lambda **_kwargs: SimpleNamespace(
        messages=SimpleNamespace(
            create=lambda **kwargs: calls.append(kwargs) or SimpleNamespace(content=[])
        )
    )
    monkeypatch.setitem(sys.modules, "anthropic", anthropic)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("ANTHROPIC_EVAL_MODEL", raising=False)

    client = providers.AnthropicModelClient()
    client.generate(case_id="case", messages=[], system="system")

    assert calls[0]["model"] == "claude-sonnet-5-5"
    assert calls[0]["max_tokens"] == 16000
    assert calls[0]["thinking"] == {"type": "adaptive"}
    assert calls[0]["output_config"] == {"effort": "medium"}
    assert "temperature" not in calls[0]


@pytest.mark.parametrize("model", ["claude-opus-5-5", "claude-haiku-4-5"])
def test_anthropic_client_runtime_model_override_uses_compatible_settings(monkeypatch, model):
    calls = []
    anthropic = ModuleType("anthropic")
    anthropic.Anthropic = lambda **_kwargs: SimpleNamespace(
        messages=SimpleNamespace(
            create=lambda **kwargs: calls.append(kwargs) or SimpleNamespace(content=[])
        )
    )
    monkeypatch.setitem(sys.modules, "anthropic", anthropic)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_EVAL_MODEL", model)

    providers.AnthropicModelClient().generate(case_id="case", messages=[], system="")

    assert calls[0]["model"] == model
    assert "thinking" not in calls[0]
    if model == "claude-haiku-4-5":
        assert calls[0]["temperature"] == providers.DEFAULT_TEMPERATURE
        assert "output_config" not in calls[0]
    else:
        assert calls[0]["output_config"] == {"effort": "medium"}
        assert "temperature" not in calls[0]


def test_anthropic_client_translates_text_and_tool_blocks(monkeypatch):
    calls = []
    response = SimpleNamespace(
        content=[
            _block(type="text", text="Result: "),
            _block(
                type="tool_use", id="tool-1", name="validate_reform", input={"x": 1}
            ),
            _block(
                type="tool_use", id="tool-2", name="ignored_input", input="not a dict"
            ),
            _block(type="thinking", thinking="private reasoning", signature="signature"),
            _block(type="text", text="done"),
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
    monkeypatch.setenv("ANTHROPIC_EVAL_MODEL", "test-model")

    client = providers.AnthropicModelClient(max_tokens=123)
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
    assert "thinking" not in calls[0]
    assert calls[0]["output_config"] == {"effort": "medium"}
    assert "temperature" not in calls[0]
    assert turn.assistant_content == [block.model_dump() for block in response.content]


def test_anthropic_tool_loop_replays_signed_thinking_and_block_order_through_sdk():
    calls = []
    content = [
        {"type": "thinking", "thinking": "Choose the calculation.", "signature": "signed"},
        {"type": "redacted_thinking", "data": "opaque"},
        {"type": "text", "text": "Calculating. "},
        {
            "type": "tool_use",
            "id": "toolu_household",
            "name": "household_analysis",
            "input": {"description": "One adult", "year": 2026},
        },
        {"type": "text", "text": "Checking the result. "},
    ]

    def handle_request(request):
        payload = json.loads(request.content)
        calls.append(payload)
        if len(calls) == 2:
            assert payload["messages"][-2] == {"role": "assistant", "content": content}
            assert payload["messages"][-1]["content"][0]["tool_use_id"] == "toolu_household"
        return httpx.Response(
            200,
            json={
                "id": f"msg_eval_{len(calls)}",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-5-5",
                "content": content if len(calls) == 1 else [{"type": "text", "text": "Done."}],
                "stop_reason": "tool_use" if len(calls) == 1 else "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )

    case = ToolLoopCase(
        id="signed_thinking_loop",
        description="Replay provider content between a tool call and the final answer.",
        prompt="Calculate, then answer.",
        expected_tools=[ToolCallExpectation(name="household_analysis")],
        expect=TextExpectation(required=["Done."]),
        capability_outputs=[
            FrozenToolCall(
                name="household_analysis",
                output_fixture="capability_outputs/household_completed.json",
            )
        ],
    )
    client = object.__new__(providers.AnthropicModelClient)
    client.model = "claude-sonnet-5-5"
    client.max_tokens = 16000
    with Anthropic(
        api_key="test-key",
        http_client=httpx.Client(transport=httpx.MockTransport(handle_request)),
    ) as sdk_client:
        client.client = sdk_client
        result = runner._run_tool_loop(case, client)

    assert result.status == "passed", result.errors
    assert len(calls) == 2
    for payload in calls:
        assert payload["thinking"] == {"type": "adaptive"}
        assert payload["output_config"] == {"effort": "medium"}
        assert payload["max_tokens"] == 16000
        assert "temperature" not in payload


def test_anthropic_client_fails_on_refusal_before_reading_content():
    client = object.__new__(providers.AnthropicModelClient)
    client.client = SimpleNamespace(
        messages=SimpleNamespace(
            create=lambda **_kwargs: SimpleNamespace(stop_reason="refusal")
        )
    )
    client.model = "claude-sonnet-5-5"
    client.max_tokens = 16000

    with pytest.raises(RuntimeError, match="Anthropic refused evaluation case refused-case"):
        client.generate(case_id="refused-case", messages=[], system="")


def test_anthropic_client_omits_empty_tools(monkeypatch):
    client = object.__new__(providers.AnthropicModelClient)
    calls = []
    client.client = SimpleNamespace(
        messages=SimpleNamespace(
            create=lambda **kwargs: calls.append(kwargs) or SimpleNamespace(content=[])
        )
    )
    client.model = "test-model"
    client.max_tokens = 50

    assert client.generate(case_id="case", messages=[], system="", tools=[]).text == ""
    assert "tools" not in calls[0]


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["eval"], {"suite": None, "mode": "offline", "provider": None}),
        (
            ["eval", "--suite", "trajectory", "--suite", "answer", "--mode", "live", "--provider", "anthropic"],
            {"suite": ["trajectory", "answer"], "mode": "live", "provider": "anthropic"},
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


def test_write_report_creates_json_and_markdown_files(tmp_path):
    report = _report()

    json_path, markdown_path = reporting.write_report(report, tmp_path / "reports")

    assert json_path.name == "20260721T1200000000-offline.json"
    assert json_path.exists()
    assert '"provider": "offline"' in json_path.read_text()
    assert markdown_path.exists()
    assert "# UK Chat AI Eval Report" in markdown_path.read_text()
