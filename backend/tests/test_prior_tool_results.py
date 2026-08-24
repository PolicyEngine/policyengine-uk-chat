"""Cross-turn tool result carry-over.

A follow-up turn cannot recompute what an earlier turn established, and the
runtime keeps no server-side turn state, so earlier tool output has to travel
back with the request. These tests pin the typed contract, the bounds that keep
it from growing without limit, and the rendered block the model reads.
"""

from chat.prior_results import (
    BLOCK_HEADER,
    MAX_BLOCK_CHARS,
    MAX_PRIOR_RESULTS,
    MAX_RESULT_CHARS,
    PriorToolResult,
    bound_prior_results,
    render_established_results_block,
)
from chat.schemas import ChatRequest
from chat.system_blocks import build_system_blocks
from chat.turn_input import prepare_turn_input


def _result(index: int, payload: str = "{}") -> PriorToolResult:
    return PriorToolResult(tool_name=f"tool_{index}", result=payload)


def test_no_prior_results_renders_no_block():
    assert render_established_results_block([]) is None


def test_block_names_the_source_tool_and_its_arguments():
    block = render_established_results_block(
        [
            PriorToolResult(
                tool_name="run_household_simulation",
                result='{"universal_credit": 1798.8}',
                tool_input={"year": 2026},
            )
        ]
    )

    assert BLOCK_HEADER in block
    assert "run_household_simulation" in block
    assert "{'year': 2026}" in block
    assert '{"universal_credit": 1798.8}' in block


def test_block_forbids_contradiction_and_still_requires_computing():
    block = render_established_results_block([_result(1)])

    assert "Do not state a number that contradicts them" in block
    assert "not a substitute for computing" in block


def test_only_the_most_recent_results_are_kept():
    bounded = bound_prior_results([_result(index) for index in range(MAX_PRIOR_RESULTS + 5)])

    assert len(bounded) == MAX_PRIOR_RESULTS
    # Oldest dropped, order preserved so the block reads in computation order.
    assert bounded[0].tool_name == "tool_5"
    assert bounded[-1].tool_name == f"tool_{MAX_PRIOR_RESULTS + 4}"


def test_each_result_is_truncated_to_its_budget():
    bounded = bound_prior_results([_result(1, "x" * (MAX_RESULT_CHARS + 500))])

    assert len(bounded[0].result) == MAX_RESULT_CHARS + len("…[truncated]")
    assert bounded[0].result.endswith("…[truncated]")


def test_the_rendered_block_stays_within_its_budget():
    block = render_established_results_block(
        [_result(index, "x" * MAX_RESULT_CHARS) for index in range(MAX_PRIOR_RESULTS)]
    )

    assert len(block) <= MAX_BLOCK_CHARS + len("…[truncated]")


# --- request contract -------------------------------------------------------


def test_prior_results_are_collected_from_the_typed_field():
    turn = prepare_turn_input(
        ChatRequest(
            messages=[
                {"role": "user", "content": "Cost of the reform?"},
                {
                    "role": "assistant",
                    "content": "It costs £1.2bn.",
                    "tool_results": [
                        {
                            "tool_name": "compute_budgetary_impact",
                            "result": '{"budgetary_impact": 1200000000}',
                            "tool_input": {"simulation_id": "society_1"},
                        }
                    ],
                },
                {"role": "user", "content": "And by decile?"},
            ],
            session_id="session-1",
        )
    )

    assert len(turn.prior_tool_results) == 1
    carried = turn.prior_tool_results[0]
    assert carried.tool_name == "compute_budgetary_impact"
    assert carried.tool_input == {"simulation_id": "society_1"}
    # The transcript itself is unchanged: results travel beside it, not inside.
    assert turn.messages[1] == {"role": "assistant", "content": "It costs £1.2bn."}


def test_requests_without_prior_results_stay_empty():
    turn = prepare_turn_input(
        ChatRequest(messages=[{"role": "user", "content": "Hello"}])
    )

    assert turn.prior_tool_results == ()


def test_prior_results_are_bounded_at_the_request_boundary():
    turn = prepare_turn_input(
        ChatRequest(
            messages=[
                {
                    "role": "assistant",
                    "content": "Reply",
                    "tool_results": [
                        {"tool_name": f"tool_{index}", "result": "{}"}
                        for index in range(MAX_PRIOR_RESULTS + 3)
                    ],
                },
                {"role": "user", "content": "Next"},
            ]
        )
    )

    assert len(turn.prior_tool_results) == MAX_PRIOR_RESULTS


# --- system block assembly --------------------------------------------------


def test_established_results_are_appended_after_the_cache_breakpoint():
    blocks = build_system_blocks(
        established_results=render_established_results_block([_result(1)])
    )

    assert len(blocks) == 2
    assert "cache_control" in blocks[0]
    assert "cache_control" not in blocks[1]
    assert BLOCK_HEADER in blocks[1]["text"]


def test_no_established_block_is_added_when_there_is_nothing_to_carry():
    assert len(build_system_blocks(established_results=None)) == 1
