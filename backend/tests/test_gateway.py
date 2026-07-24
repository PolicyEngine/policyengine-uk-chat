"""Offline tests for the chat gateway.

Two layers, both network-free: the deterministic gate/criticality policy
(`gateway.policy`, pure) and the `run_gateway` parser (exercised with a stubbed
Anthropic client). The live classifier itself is covered by the `gateway` eval
suite, not here.
"""

from types import SimpleNamespace
from unittest.mock import patch

from gateway.policy import (
    TOOL_SLOT_REQUIREMENT,
    SlotFact,
    criticality,
    gate,
    is_inferable,
)
from gateway.runtime import _verdict_from_plan


def sf(name, source, kind="tool_input", value=None):
    return SlotFact(name=name, source=source, kind=kind, value=value)


class TestSlotInventory:
    """The slot inventory is derived from TOOL_DEFINITIONS so it can't drift."""

    def test_required_slots_detected(self):
        assert TOOL_SLOT_REQUIREMENT[("run_household_simulation", "people")] == "required"
        assert TOOL_SLOT_REQUIREMENT[("run_axes_simulation", "axis")] == "required"
        assert TOOL_SLOT_REQUIREMENT[("run_axes_simulation", "outputs")] == "required"
        assert TOOL_SLOT_REQUIREMENT[("validate_reform", "reform")] == "required"
        assert TOOL_SLOT_REQUIREMENT[("aggregate_result", "simulation_id")] == "required"
        assert TOOL_SLOT_REQUIREMENT[("aggregate_result", "operation")] == "required"

    def test_defaulted_slots_detected(self):
        assert TOOL_SLOT_REQUIREMENT[("run_society_simulation", "dataset")] == "defaulted"
        assert TOOL_SLOT_REQUIREMENT[("run_household_simulation", "year")] == "defaulted"
        assert TOOL_SLOT_REQUIREMENT[("get_parameter", "year")] == "defaulted"

    def test_optional_undefaulted_slots_detected(self):
        assert TOOL_SLOT_REQUIREMENT[("run_household_simulation", "reform")] == "optional"


class TestCriticality:
    def test_required_is_high(self):
        assert criticality("run_household_simulation", sf("people", "assumed")) == "high"

    def test_defaulted_is_low(self):
        assert criticality("run_society_simulation", sf("dataset", "default")) == "low"

    def test_output_is_high(self):
        assert criticality("run_society_simulation", sf("output", "assumed", kind="output")) == "high"

    def test_econ_reform_override_high(self):
        # Schema marks reform optional, but the curated override makes it high:
        # a society-wide sim with no reform is never the intent.
        assert criticality("run_society_simulation", sf("reform", "assumed")) == "high"

    def test_enhanced_frs_default_is_not_overridden_for_wealth(self):
        assert criticality(
            "run_society_simulation", sf("dataset", "assumed"), prompt="model a wealth tax"
        ) == "low"

    def test_dataset_not_promoted_for_income(self):
        assert criticality(
            "run_society_simulation", sf("dataset", "assumed"),
            prompt="raise the personal allowance to 15000",
        ) == "low"

    def test_year_promoted_to_medium_on_nondefault(self):
        assert criticality(
            "run_household_simulation", sf("year", "assumed"), prompt="their benefits in 2023"
        ) == "medium"


class TestInferable:
    def test_benunit_household_inferable(self):
        assert is_inferable("run_household_simulation", "benunit")
        assert is_inferable("run_household_simulation", "household")
        assert is_inferable("run_axes_simulation", "benunit")
        assert is_inferable("run_axes_simulation", "household")

    def test_people_not_inferable(self):
        assert not is_inferable("run_household_simulation", "people")


class TestGate:
    def test_ready_all_grounded(self):
        r = gate(
            True, "run_society_simulation",
            [sf("reform", "prompt"), sf("dataset", "default"), sf("output", "prompt", kind="output")],
            [],
        )
        assert r.outcome == "ready" and r.gating_slots == []

    def test_needs_plan_assumed_required(self):
        r = gate(
            True, "run_society_simulation",
            [sf("reform", "assumed"), sf("output", "assumed", kind="output")],
            [], prompt="compare the two reforms",
        )
        assert r.outcome == "needs_plan"
        assert set(r.gating_slots) == {"reform", "output"}

    def test_inferable_slots_do_not_gate(self):
        # benunit/household assumed, but inferable → no question → ready.
        r = gate(
            True, "run_household_simulation",
            [sf("people", "prompt"), sf("benunit", "assumed"), sf("household", "assumed")],
            [],
        )
        assert r.outcome == "ready"

    def test_default_source_does_not_gate(self):
        r = gate(
            True, "run_society_simulation",
            [sf("reform", "prompt"), sf("dataset", "default")], [],
        )
        assert r.outcome == "ready"

    def test_low_criticality_assumed_does_not_gate(self):
        r = gate(
            True, "aggregate_result",
            [sf("simulation_id", "prompt"), sf("entity", "prompt"), sf("variable", "prompt"), sf("operation", "prompt"), sf("group_by", "assumed")], [],
        )
        assert r.outcome == "ready"

    def test_wealth_dataset_default_does_not_gate(self):
        r = gate(
            True, "run_society_simulation",
            [sf("reform", "prompt"), sf("dataset", "assumed")], [],
            prompt="model a 1% wealth tax on net wealth above 10m",
        )
        assert r.outcome == "ready" and r.gating_slots == []

    def test_partial(self):
        r = gate(True, "run_society_simulation", [sf("reform", "prompt")], ["inflation"])
        assert r.outcome == "partial"

    def test_partial_precedes_needs_plan(self):
        # When a plan has BOTH an unmodellable output AND a slot that would gate,
        # `partial` must win (resolve scope first), not `needs_plan`. Locks the
        # documented precedence in gate() so reordering the checks can't silently
        # regress it — test_partial alone wouldn't catch that, since its grounded
        # ("prompt") slot never gates regardless of ordering.
        slots = [sf("reform", "assumed")]  # assumed + high (override) + not inferable → gates
        # Sanity: that slot really does gate on its own → needs_plan.
        assert gate(True, "run_society_simulation", slots, []).outcome == "needs_plan"
        # With an unmodellable output also present, partial takes precedence.
        assert gate(True, "run_society_simulation", slots, ["inflation"]).outcome == "partial"

    def test_out_of_scope_no_tool_in_domain(self):
        assert gate(True, None, [], []).outcome == "out_of_scope"
        assert gate(True, None, [], ["inflation"]).outcome == "out_of_scope"

    def test_irrelevant_not_in_domain(self):
        assert gate(False, None, [], []).outcome == "irrelevant"


def test_gateway_plan_gates_omitted_required_axes_slots():
    verdict = _verdict_from_plan(
        {
            "in_domain": True,
            "tool": "run_axes_simulation",
            "slots": [],
            "unmodellable_outputs": [],
        },
        "Show household net income from £0 to £100,000.",
    )

    assert verdict.outcome == "needs_plan"
    assert verdict.route == "lightweight"
    assert set(verdict.gating_slots) == {"people", "axis", "outputs"}
    assert {
        slot.name
        for slot in verdict.slots
        if slot.source == "assumed"
    } == {"people", "axis", "outputs"}


def test_gateway_plan_treats_blank_required_axes_slot_as_unresolved():
    verdict = _verdict_from_plan(
        {
            "in_domain": True,
            "tool": "run_axes_simulation",
            "slots": [
                {
                    "name": "people",
                    "kind": "tool_input",
                    "value": "single adult aged 30",
                    "source": "prompt",
                },
                {
                    "name": "axis",
                    "kind": "tool_input",
                    "value": "",
                    "source": "prompt",
                },
                {
                    "name": "outputs",
                    "kind": "tool_input",
                    "value": "household net income",
                    "source": "prompt",
                },
            ],
            "unmodellable_outputs": [],
        },
        "Show household net income from £0 to £100,000.",
    )

    assert verdict.outcome == "needs_plan"
    assert verdict.gating_slots == ["axis"]
    axis = next(slot for slot in verdict.slots if slot.name == "axis")
    assert axis.source == "assumed"
    assert axis.value is None


def _stub_client(plan):
    block = SimpleNamespace(type="tool_use", name="emit_plan", input=plan)
    resp = SimpleNamespace(content=[block])
    return SimpleNamespace(messages=SimpleNamespace(create=lambda **k: resp))


def _raises(*_a, **_k):
    raise RuntimeError("api down")


class TestRunGateway:
    def test_parses_ready(self):
        from gateway import runtime as gateway
        plan = {
            "in_domain": True, "tool": "run_society_simulation",
            "slots": [
                {"name": "reform", "kind": "tool_input", "value": "PA 15k", "source": "prompt"},
                {"name": "output", "kind": "output", "value": "budgetary_impact", "source": "prompt"},
            ],
            "unmodellable_outputs": [],
        }
        with patch.object(gateway, "get_sync_client", lambda: _stub_client(plan)):
            v = gateway.run_gateway("cost of raising the PA to 15000?")
        assert v.outcome == "ready" and v.route == "compute"
        assert v.tool == "run_society_simulation"

    def test_parses_needs_plan(self):
        from gateway import runtime as gateway
        plan = {
            "in_domain": True, "tool": "run_society_simulation",
            "slots": [
                {"name": "reform", "kind": "tool_input", "source": "assumed"},
                {"name": "output", "kind": "output", "source": "assumed"},
            ],
            "unmodellable_outputs": [],
        }
        with patch.object(gateway, "get_sync_client", lambda: _stub_client(plan)):
            v = gateway.run_gateway("compare the two reforms")
        assert v.outcome == "needs_plan" and v.route == "lightweight"
        assert set(v.gating_slots) == {"reform", "output"}

    def test_unknown_tool_becomes_none(self):
        from gateway import runtime as gateway
        plan = {"in_domain": True, "tool": "none", "slots": [], "unmodellable_outputs": ["inflation"]}
        with patch.object(gateway, "get_sync_client", lambda: _stub_client(plan)):
            v = gateway.run_gateway("what will inflation be?")
        assert v.tool is None and v.outcome == "out_of_scope"

    def test_empty_input_fail_safe(self):
        from gateway import runtime as gateway
        # The client must not be called for empty input.
        with patch.object(gateway, "get_sync_client", _raises):
            assert gateway.run_gateway("").outcome == "ready"
            assert gateway.run_gateway("   ").outcome == "ready"

    def test_api_error_fail_safe(self):
        from gateway import runtime as gateway
        with patch.object(gateway, "get_sync_client", _raises):
            v = gateway.run_gateway("anything at all")
        assert v.outcome == "ready" and v.route == "compute"

    def test_missing_plan_block_fail_safe(self):
        from gateway import runtime as gateway
        resp = SimpleNamespace(content=[SimpleNamespace(type="text", text="oops")])
        client = SimpleNamespace(messages=SimpleNamespace(create=lambda **k: resp))
        with patch.object(gateway, "get_sync_client", lambda: client):
            assert gateway.run_gateway("anything").outcome == "ready"

    def test_empty_plan_dict_fail_safe(self):
        # A degenerate emit_plan with empty input must fall back to compute, NOT
        # be read as an out_of_scope refusal of a genuinely in-scope question.
        from gateway import runtime as gateway
        with patch.object(gateway, "get_sync_client", lambda: _stub_client({})):
            v = gateway.run_gateway("What is the budgetary cost of raising the PA to 15000?")
        assert v.outcome == "ready" and v.route == "compute"

    def test_plan_missing_tool_fail_safe(self):
        # A plan missing the routing decision is a parse failure → fail-safe.
        from gateway import runtime as gateway
        plan = {"in_domain": True, "rationale": "partial output only"}
        with patch.object(gateway, "get_sync_client", lambda: _stub_client(plan)):
            assert gateway.run_gateway("anything in scope").outcome == "ready"

    def test_bad_source_coerced_to_assumed(self):
        from gateway import runtime as gateway
        plan = {
            "in_domain": True, "tool": "run_society_simulation",
            "slots": [{"name": "reform", "kind": "tool_input", "source": "garbage"}],
            "unmodellable_outputs": [],
        }
        with patch.object(gateway, "get_sync_client", lambda: _stub_client(plan)):
            v = gateway.run_gateway("do a reform")
        # garbage source → assumed → reform (high) gates → needs_plan
        assert v.outcome == "needs_plan" and v.gating_slots == ["reform"]


class TestGatewaySystemPrompt:
    def test_rendered_prompt_contains_default_simulation_year(self):
        # The gate in gateway.policy keys its non-default-year detection off
        # DEFAULT_SIMULATION_YEAR; the rendered classifier prompt must state the
        # same year, so a year bump can't leave the prompt describing the old
        # default. Compare against the imported constant, never a literal.
        from gateway import runtime as gateway
        from tools.definitions import DEFAULT_SIMULATION_YEAR

        assert f"year {DEFAULT_SIMULATION_YEAR}" in gateway.GATEWAY_SYSTEM
        assert "{default_year}" not in gateway.GATEWAY_SYSTEM


class TestWriterDirective:
    def test_needs_plan_lists_slots(self):
        from gateway import runtime as gateway
        v = gateway.GatewayVerdict(outcome="needs_plan", route="lightweight", gating_slots=["reform", "output"])
        d = gateway.gateway_writer_directive(v)
        assert "reform" in d and "output" in d

    def test_partial_lists_unmodellable(self):
        from gateway import runtime as gateway
        v = gateway.GatewayVerdict(outcome="partial", route="lightweight", unmodellable_outputs=["inflation"])
        assert "inflation" in gateway.gateway_writer_directive(v)

    def test_serialise_plan_for_ready(self):
        from gateway import runtime as gateway
        v = gateway.GatewayVerdict(
            outcome="ready", route="compute", tool="run_society_simulation",
            slots=[gateway.SlotFact("reform", "prompt", value="PA 15k")],
        )
        s = gateway.serialise_plan_for_system(v)
        assert "run_society_simulation" in s and "reform=PA 15k" in s
