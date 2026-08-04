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
    complete_slots,
    criticality,
    gate,
    is_inferable,
)


def sf(name, source, kind="tool_input", value=None):
    return SlotFact(name=name, source=source, kind=kind, value=value)


class TestSlotInventory:
    """The slot inventory is derived from TOOL_DEFINITIONS so it can't drift."""

    def test_required_slots_detected(self):
        assert TOOL_SLOT_REQUIREMENT[("run_household_simulation", "people")] == "required"
        assert TOOL_SLOT_REQUIREMENT[("validate_reform", "reform")] == "required"
        assert TOOL_SLOT_REQUIREMENT[("aggregate_result", "simulation_id")] == "required"
        assert TOOL_SLOT_REQUIREMENT[("aggregate_result", "operation")] == "required"

    def test_defaulted_slots_detected(self):
        assert TOOL_SLOT_REQUIREMENT[("run_household_simulation", "year")] == "defaulted"
        assert TOOL_SLOT_REQUIREMENT[("get_parameter", "year")] == "defaulted"
        assert ("run_society_simulation", "dataset") not in TOOL_SLOT_REQUIREMENT

    def test_optional_undefaulted_slots_detected(self):
        assert TOOL_SLOT_REQUIREMENT[("run_household_simulation", "reform")] == "optional"


class TestCriticality:
    def test_required_is_high(self):
        assert criticality("run_household_simulation", sf("people", "assumed")) == "high"

    def test_output_is_high(self):
        assert criticality("run_society_simulation", sf("output", "assumed", kind="output")) == "high"

    def test_econ_reform_override_high(self):
        # Schema marks reform optional, but the curated override makes it high:
        # a society-wide sim with no reform is never the intent.
        assert criticality("run_society_simulation", sf("reform", "assumed")) == "high"

    def test_year_promoted_to_medium_on_nondefault(self):
        assert criticality(
            "run_household_simulation", sf("year", "assumed"), prompt="their benefits in 2023"
        ) == "medium"


class TestInferable:
    def test_benunit_household_inferable(self):
        assert is_inferable("run_household_simulation", "benunit")
        assert is_inferable("run_household_simulation", "household")

    def test_people_not_inferable(self):
        assert not is_inferable("run_household_simulation", "people")


class TestGate:
    def test_ready_all_grounded(self):
        r = gate(
            True, "run_society_simulation",
            [sf("reform", "prompt"), sf("output", "prompt", kind="output")],
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
            [sf("reform", "prompt")], [],
        )
        assert r.outcome == "ready"

    def test_low_criticality_assumed_does_not_gate(self):
        r = gate(
            True, "aggregate_result",
            [sf("simulation_id", "prompt"), sf("entity", "prompt"), sf("variable", "prompt"), sf("operation", "prompt"), sf("group_by", "assumed")], [],
        )
        assert r.outcome == "ready"

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


class TestSlotCompletion:
    def test_adds_every_missing_tool_schema_slot_and_output(self):
        completed = complete_slots("run_society_simulation", [])

        assert {
            slot.name
            for slot in completed
            if slot.kind == "tool_input"
        } == {
            name
            for (tool, name) in TOOL_SLOT_REQUIREMENT
            if tool == "run_society_simulation"
        }
        assert all(slot.source == "assumed" for slot in completed)
        assert any(slot.kind == "output" and slot.name == "output" for slot in completed)

    def test_keeps_model_grounded_slots(self):
        completed = complete_slots(
            "run_society_simulation",
            [
                sf("reform", "prompt", value="Raise the basic rate to 21%"),
                sf("output", "prompt", kind="output", value="budgetary_impact"),
            ],
        )

        reform = next(slot for slot in completed if slot.name == "reform")
        output = next(slot for slot in completed if slot.kind == "output")
        assert reform.source == "prompt"
        assert output.source == "prompt"

    def test_missing_material_slots_gate_instead_of_reaching_compute(self):
        completed = complete_slots("run_society_simulation", [])

        result = gate(True, "run_society_simulation", completed, [])

        assert result.outcome == "needs_plan"
        assert set(result.gating_slots) == {"reform", "output"}

    def test_missing_inferable_household_slots_do_not_gate(self):
        completed = complete_slots(
            "run_household_simulation",
            [
                sf("people", "prompt", value="one adult aged 30"),
                sf("output", "prompt", kind="output", value="net_income"),
            ],
        )

        result = gate(True, "run_household_simulation", completed, [])

        assert result.outcome == "ready"


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

    def test_ignores_unmodellable_output_without_prompt_evidence(self):
        from gateway import runtime as gateway

        plan = {
            "in_domain": True,
            "tool": "run_society_simulation",
            "slots": [
                {
                    "name": "reform",
                    "kind": "tool_input",
                    "value": "Raise the basic rate by one percentage point",
                    "source": "prompt",
                },
                {
                    "name": "output",
                    "kind": "output",
                    "value": "tax_revenue",
                    "source": "prompt",
                },
            ],
            "unmodellable_outputs": [
                {
                    "name": "behavioural response",
                    "evidence": "behavioural response",
                }
            ],
            "catalogue_queries": [],
        }
        prompt = (
            "What would be the annual revenue from raising the basic income tax "
            "rate by one percentage point?"
        )

        with patch.object(gateway, "get_sync_client", lambda: _stub_client(plan)):
            verdict = gateway.run_gateway(prompt)

        assert verdict.outcome == "ready"
        assert verdict.route == "compute"
        assert verdict.unmodellable_outputs == []

    def test_preserves_explicit_evidence_for_an_unmodellable_output(self):
        from gateway import runtime as gateway

        plan = {
            "in_domain": True,
            "tool": "run_society_simulation",
            "slots": [
                {
                    "name": "reform",
                    "kind": "tool_input",
                    "value": "Raise the basic rate by one percentage point",
                    "source": "prompt",
                },
                {
                    "name": "output",
                    "kind": "output",
                    "value": "tax_revenue",
                    "source": "prompt",
                },
            ],
            "unmodellable_outputs": [
                {
                    "name": "behavioural response",
                    "evidence": "including behavioural responses",
                }
            ],
            "catalogue_queries": [],
        }
        prompt = (
            "What would be the annual revenue from raising the basic income tax "
            "rate by one percentage point, including behavioural responses?"
        )

        with patch.object(gateway, "get_sync_client", lambda: _stub_client(plan)):
            verdict = gateway.run_gateway(prompt)

        assert verdict.outcome == "partial"
        assert verdict.route == "lightweight"
        assert verdict.unmodellable_outputs == ["behavioural response"]

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
        # garbage source → assumed → reform (high) gates. The omitted output
        # is also material, so server-side completion marks it assumed too.
        assert v.outcome == "needs_plan"
        assert set(v.gating_slots) == {"reform", "output"}

    def test_omitted_material_slots_are_treated_as_assumed(self):
        from gateway import runtime as gateway

        plan = {
            "in_domain": True,
            "tool": "run_society_simulation",
            "slots": [],
            "unmodellable_outputs": [],
            "catalogue_queries": [],
        }
        with patch.object(gateway, "get_sync_client", lambda: _stub_client(plan)):
            v = gateway.run_gateway("Model a tax reform")

        assert v.outcome == "needs_plan"
        assert set(v.gating_slots) == {"reform", "output"}

    def test_empty_prompt_values_are_treated_as_assumed(self):
        from gateway import runtime as gateway

        plan = {
            "in_domain": True,
            "tool": "run_society_simulation",
            "slots": [
                {"name": "reform", "kind": "tool_input", "source": "prompt"},
                {"name": "output", "kind": "output", "source": "prompt"},
            ],
            "unmodellable_outputs": [],
            "catalogue_queries": [],
        }
        with patch.object(gateway, "get_sync_client", lambda: _stub_client(plan)):
            v = gateway.run_gateway("Model a tax reform")

        assert v.outcome == "needs_plan"
        assert set(v.gating_slots) == {"reform", "output"}

    def test_named_output_slot_is_grounded_without_a_duplicate_value(self):
        from gateway import runtime as gateway

        plan = {
            "in_domain": True,
            "tool": "run_society_simulation",
            "slots": [
                {
                    "name": "reform",
                    "kind": "tool_input",
                    "source": "prompt",
                    "value": "Raise the basic rate to 21%",
                },
                {"name": "decile_impact", "kind": "output", "source": "prompt"},
            ],
            "unmodellable_outputs": [],
            "catalogue_queries": [],
        }
        with patch.object(gateway, "get_sync_client", lambda: _stub_client(plan)):
            v = gateway.run_gateway("Show the decile impact of raising the basic rate to 21%")

        output = next(slot for slot in v.slots if slot.kind == "output")
        assert v.outcome == "ready"
        assert output.value == "decile_impact"

    def test_documented_current_law_baseline_can_be_defaulted(self):
        from gateway import runtime as gateway

        plan = {
            "in_domain": True,
            "tool": "run_society_simulation",
            "slots": [
                {"name": "reform", "kind": "tool_input", "source": "default"},
                {
                    "name": "output",
                    "kind": "output",
                    "source": "prompt",
                    "value": "budgetary_impact",
                },
            ],
            "unmodellable_outputs": [],
            "catalogue_queries": [],
        }
        with patch.object(gateway, "get_sync_client", lambda: _stub_client(plan)):
            v = gateway.run_gateway("What is current child benefit spending?")

        assert v.outcome == "ready"


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

    def test_unmodellable_output_schema_requires_prompt_evidence(self):
        from gateway import runtime as gateway

        unmodellable = gateway._EMIT_PLAN_TOOL["input_schema"]["properties"][
            "unmodellable_outputs"
        ]

        assert unmodellable["items"]["type"] == "object"
        assert unmodellable["items"]["required"] == ["name", "evidence"]
        assert (
            "exact quote"
            in unmodellable["items"]["properties"]["evidence"]["description"]
        )

    def test_domain_and_capability_schema_require_grounded_decisions(self):
        from gateway import runtime as gateway

        schema = gateway._EMIT_PLAN_TOOL["input_schema"]

        assert "domain" in schema["required"]
        assert "capability" in schema["required"]
        assert schema["properties"]["domain"]["required"] == ["status"]
        assert schema["properties"]["capability"]["required"] == ["status"]
        assert "exact quote" in schema["properties"]["domain"]["description"]
        assert "exact quote" in schema["properties"]["capability"]["description"]


class TestGatewayDecisionEvidence:
    def test_accepts_exactly_quoted_negative_decisions(self):
        from gateway import runtime as gateway

        plan = {
            "domain": {"status": "explicit_non_uk", "evidence": "US federal"},
            "capability": {
                "status": "catalogue_uncertain",
                "evidence": "federal income tax",
            },
            "tool": "none",
            "slots": [],
            "catalogue_queries": [],
        }
        with patch.object(gateway, "get_sync_client", lambda: _stub_client(plan)):
            verdict = gateway.run_gateway("How would US federal income tax change?")

        assert verdict.domain.status == "explicit_non_uk"
        assert verdict.domain.evidence == "US federal"
        assert verdict.capability.status == "catalogue_uncertain"
        assert verdict.capability.evidence == "federal income tax"

    def test_rejects_invented_negative_decision_evidence(self):
        from gateway import runtime as gateway

        plan = {
            "domain": {"status": "unrelated", "evidence": "write Python"},
            "capability": {
                "status": "explicitly_unmodellable",
                "evidence": "employment effects",
            },
            "tool": "none",
            "slots": [],
            "catalogue_queries": [],
        }
        with patch.object(gateway, "get_sync_client", lambda: _stub_client(plan)):
            verdict = gateway.run_gateway("What is the cost of increasing UC?")

        assert verdict.domain.status == "uk_or_unspecified"
        assert verdict.domain.evidence is None
        assert verdict.capability.status == "supported"
        assert verdict.capability.evidence is None


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
