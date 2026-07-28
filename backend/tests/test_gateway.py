"""Offline tests for the chat gateway.

Two layers, both network-free: the deterministic gate/criticality policy
(`gateway.policy`, pure) and the `run_gateway` parser (exercised with a stubbed
Anthropic client). The live classifier itself is covered by the `gateway` eval
suite, not here.
"""

from types import SimpleNamespace
from unittest.mock import patch

from gateway.policy import (
    OUTPUT_VOCAB,
    TOOL_SLOT_REQUIREMENT,
    SlotFact,
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

    def test_internal_parameter_path_is_inferable(self):
        assert is_inferable("get_parameter", "path")


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

    def test_internal_parameter_path_does_not_gate(self):
        r = gate(
            True,
            "get_parameter",
            [sf("path", "assumed"), sf("year", "prompt")],
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
                {
                    "name": "budgetary_impact",
                    "kind": "output",
                    "source": "assumed",
                },
            ],
            "unmodellable_outputs": [],
        }
        with patch.object(gateway, "get_sync_client", lambda: _stub_client(plan)):
            v = gateway.run_gateway("compare the two reforms")
        assert v.outcome == "needs_plan" and v.route == "lightweight"
        assert set(v.gating_slots) == {"reform", "comparison_metric"}

    def test_vague_comparison_adds_assumed_metric_when_model_omits_outputs(self):
        from gateway import runtime as gateway

        plan = {
            "in_domain": True,
            "tool": "run_society_simulation",
            "slots": [
                {
                    "name": "reform",
                    "kind": "tool_input",
                    "value": "PA £15,000 versus UC £20 per week",
                    "source": "assumed",
                }
            ],
            "unmodellable_outputs": [],
        }
        prompt = (
            "Which is better, a £15,000 personal allowance or increasing "
            "Universal Credit by £20 per week?"
        )
        with patch.object(gateway, "get_sync_client", lambda: _stub_client(plan)):
            verdict = gateway.run_gateway(prompt)

        assert verdict.outcome == "needs_plan"
        assert verdict.gating_slots == ["comparison_metric"]
        assert next(
            slot for slot in verdict.slots if slot.name == "reform"
        ).source == "prompt"
        output_slots = [slot for slot in verdict.slots if slot.kind == "output"]
        assert output_slots == [
            gateway.SlotFact(
                name="comparison_metric",
                source="assumed",
                kind="output",
                value="unspecified measurable outcome",
            )
        ]

    def test_vague_comparison_replaces_model_guessed_output(self):
        from gateway import runtime as gateway

        plan = {
            "in_domain": True,
            "tool": "run_society_simulation",
            "slots": [
                {
                    "name": "reform",
                    "kind": "tool_input",
                    "source": "prompt",
                },
                {
                    "name": "budgetary_impact",
                    "kind": "output",
                    "source": "prompt",
                },
            ],
            "unmodellable_outputs": [],
        }
        with patch.object(gateway, "get_sync_client", lambda: _stub_client(plan)):
            verdict = gateway.run_gateway("Which of these two reforms is better?")

        assert verdict.gating_slots == ["comparison_metric"]
        assert [slot.name for slot in verdict.slots if slot.kind == "output"] == [
            "comparison_metric"
        ]

    def test_vague_comparison_does_not_ground_hallucinated_reform_values(self):
        from gateway import runtime as gateway

        plan = {
            "in_domain": True,
            "tool": "run_society_simulation",
            "slots": [
                {
                    "name": "reform",
                    "kind": "tool_input",
                    "value": "PA £15,000 versus UC £20 per week",
                    "source": "assumed",
                }
            ],
            "unmodellable_outputs": [],
        }
        with patch.object(gateway, "get_sync_client", lambda: _stub_client(plan)):
            verdict = gateway.run_gateway(
                "Compare raising the personal allowance with increasing "
                "Universal Credit."
            )

        assert verdict.gating_slots == ["reform", "comparison_metric"]
        assert next(
            slot for slot in verdict.slots if slot.name == "reform"
        ).source == "assumed"

    def test_explicit_comparison_metric_is_grounded_from_prompt(self):
        from gateway import runtime as gateway

        plan = {
            "in_domain": True,
            "tool": "run_society_simulation",
            "slots": [
                {
                    "name": "reform",
                    "kind": "tool_input",
                    "source": "prompt",
                }
            ],
            "unmodellable_outputs": [],
        }
        with patch.object(gateway, "get_sync_client", lambda: _stub_client(plan)):
            verdict = gateway.run_gateway(
                "Compare these two fully specified reforms by tax revenue."
            )

        assert verdict.outcome == "ready"
        assert verdict.gating_slots == []
        assert [
            (slot.name, slot.source)
            for slot in verdict.slots
            if slot.kind == "output"
        ] == [("tax_revenue", "prompt")]

    def test_better_off_is_an_explicit_winners_losers_metric(self):
        from gateway import runtime as gateway

        plan = {
            "in_domain": True,
            "tool": "run_society_simulation",
            "slots": [
                {
                    "name": "reform",
                    "kind": "tool_input",
                    "source": "prompt",
                }
            ],
            "unmodellable_outputs": [],
        }
        with patch.object(gateway, "get_sync_client", lambda: _stub_client(plan)):
            verdict = gateway.run_gateway(
                "Compare how many people are better off or worse off under this reform."
            )

        assert verdict.outcome == "ready"
        assert [
            (slot.name, slot.source)
            for slot in verdict.slots
            if slot.kind == "output"
        ] == [("winners_losers", "prompt")]

    def test_noncomparison_missing_output_keeps_existing_fail_safe(self):
        from gateway import runtime as gateway

        plan = {
            "in_domain": True,
            "tool": "run_society_simulation",
            "slots": [
                {
                    "name": "reform",
                    "kind": "tool_input",
                    "source": "prompt",
                }
            ],
            "unmodellable_outputs": [],
        }
        with patch.object(gateway, "get_sync_client", lambda: _stub_client(plan)):
            verdict = gateway.run_gateway("Run this fully specified reform.")

        assert verdict.outcome == "ready"
        assert all(slot.kind != "output" for slot in verdict.slots)

    def test_comparison_metric_is_dropped_outside_comparisons(self):
        from gateway import runtime as gateway

        plan = {
            "in_domain": True,
            "tool": "run_society_simulation",
            "slots": [
                {
                    "name": "reform",
                    "kind": "tool_input",
                    "source": "assumed",
                },
                {
                    "name": "comparison_metric",
                    "kind": "output",
                    "source": "assumed",
                },
            ],
            "unmodellable_outputs": [],
        }
        with patch.object(gateway, "get_sync_client", lambda: _stub_client(plan)):
            verdict = gateway.run_gateway("Raise the basic rate of income tax.")

        assert verdict.gating_slots == ["reform"]
        assert all(slot.name != "comparison_metric" for slot in verdict.slots)

    def test_lookup_comparison_is_not_treated_as_a_reform_comparison(self):
        from gateway import runtime as gateway

        plan = {
            "in_domain": True,
            "tool": "get_parameter",
            "slots": [
                {
                    "name": "path",
                    "kind": "tool_input",
                    "source": "prompt",
                },
                {
                    "name": "year",
                    "kind": "tool_input",
                    "source": "prompt",
                },
            ],
            "unmodellable_outputs": [],
        }
        with patch.object(gateway, "get_sync_client", lambda: _stub_client(plan)):
            verdict = gateway.run_gateway(
                "Compare the basic rate threshold in 2025 and 2026."
            )

        assert verdict.outcome == "ready"
        assert all(slot.name != "comparison_metric" for slot in verdict.slots)

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

    def test_malformed_unmodellable_outputs_fail_safe_to_empty(self):
        from gateway import runtime as gateway
        plan = {
            "in_domain": True,
            "tool": "run_household_simulation",
            "slots": [
                {
                    "name": "people",
                    "kind": "tool_input",
                    "source": "prompt",
                }
            ],
            "unmodellable_outputs": "[]</unmodellable_outputs>",
        }
        with patch.object(gateway, "get_sync_client", lambda: _stub_client(plan)):
            verdict = gateway.run_gateway("Calculate this household.")

        assert verdict.outcome == "ready"
        assert verdict.unmodellable_outputs == []

    def test_unrequested_unmodellable_riders_do_not_force_partial(self):
        from gateway import runtime as gateway
        plan = {
            "in_domain": True,
            "tool": "run_society_simulation",
            "slots": [
                {
                    "name": "reform",
                    "kind": "tool_input",
                    "source": "prompt",
                },
                {
                    "name": "budgetary_impact",
                    "kind": "output",
                    "source": "prompt",
                },
            ],
            "unmodellable_outputs": [
                "behavioural response",
                "employment effects",
                "macroeconomic effects",
            ],
        }
        with patch.object(gateway, "get_sync_client", lambda: _stub_client(plan)):
            verdict = gateway.run_gateway(
                "What is the budgetary cost of raising the basic rate to 21%?"
            )

        assert verdict.outcome == "ready"
        assert verdict.unmodellable_outputs == []

    def test_explicit_unmodellable_rider_still_forces_partial(self):
        from gateway import runtime as gateway
        plan = {
            "in_domain": True,
            "tool": "run_society_simulation",
            "slots": [
                {
                    "name": "reform",
                    "kind": "tool_input",
                    "source": "prompt",
                },
                {
                    "name": "budgetary_impact",
                    "kind": "output",
                    "source": "prompt",
                },
            ],
            "unmodellable_outputs": [
                "inflation effect",
                "employment effects",
            ],
        }
        with patch.object(gateway, "get_sync_client", lambda: _stub_client(plan)):
            verdict = gateway.run_gateway(
                "What would raising the personal allowance cost and do to inflation?"
            )

        assert verdict.outcome == "partial"
        assert verdict.unmodellable_outputs == ["inflation effect"]

    def test_unknown_output_slot_cannot_gate_on_internal_intermediate(self):
        from gateway import runtime as gateway
        plan = {
            "in_domain": True,
            "tool": "run_society_simulation",
            "slots": [
                {
                    "name": "reform",
                    "kind": "tool_input",
                    "source": "prompt",
                },
                {
                    "name": "simulation_id",
                    "kind": "output",
                    "source": "assumed",
                    "value": "<simulation_result>",
                },
                {
                    "name": "decile_impact",
                    "kind": "output",
                    "source": "prompt",
                },
            ],
            "unmodellable_outputs": [],
        }
        with patch.object(gateway, "get_sync_client", lambda: _stub_client(plan)):
            verdict = gateway.run_gateway(
                "Show the distributional impact by decile of this reform."
            )

        assert verdict.outcome == "ready"
        assert [slot.name for slot in verdict.slots] == [
            "reform",
            "decile_impact",
        ]


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
        assert "comparison_metric" in OUTPUT_VOCAB
        assert "`comparison_metric`" in gateway.GATEWAY_SYSTEM

    def test_tool_summary_lists_schema_defaults(self):
        from gateway import runtime as gateway
        from tools.definitions import DEFAULT_SIMULATION_YEAR

        get_parameter = next(
            line
            for line in gateway.TOOL_SUMMARY.splitlines()
            if line.startswith("- get_parameter ")
        )
        assert f"Defaults: year={DEFAULT_SIMULATION_YEAR}" in get_parameter


class TestWriterDirective:
    def test_needs_plan_lists_slots(self):
        from gateway import runtime as gateway
        v = gateway.GatewayVerdict(
            outcome="needs_plan",
            route="lightweight",
            gating_slots=["reform", "comparison_metric"],
        )
        d = gateway.gateway_writer_directive(v)
        assert "reform" in d
        assert "comparison_metric" in d
        assert "household income" in d

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
