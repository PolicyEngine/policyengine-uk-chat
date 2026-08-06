"""Framework-independent model/tool orchestration for one UK Chat turn.

Builds the per-turn plan (gateway), selects the model, streams from Anthropic,
runs tools in parallel, records usage, and emits typed internal events. HTTP,
SSE projection, and billing are handled by the public and eval adapters.
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import replace
from functools import partial
from typing import Any, Dict, List

import httpx
from policyengine_observability import annotate
from policyengine_observability import asegment
from policyengine_observability import mark_ttft_attribute
from policyengine_observability import operation
from policyengine_observability import record_error
from policyengine_observability import record_event
from policyengine_observability import segment

from config import DEFAULT_FAST_MODEL, DEFAULT_TEMPERATURE, get_async_client
from gateway import run_gateway, serialise_plan_for_system
from gateway.clarifications import render_clarification
from gateway.proposals import (
    ProposalSigningError,
    append_proposal_marker,
    proposal_payload_from_verdict,
    resume_gateway_proposal,
    strip_proposal_markers_from_conversation,
)
from observability.segments import SegmentName
from tools.context import new_tool_context
from tools.dispatch import execute_tool

from chat.model_selection import is_followup, last_user_text, select_chat_model
from chat.events import (
    CancellationProbe,
    ChatUsage,
    SuggestionsGenerated,
    TextChunk,
    ThinkingCompleted,
    ToolCompleted,
    ToolStarted,
    ToolUsed,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
)
from chat.suggestions import generate_followup_suggestions
from chat.system_blocks import (
    build_lightweight_system_blocks,
    build_system_blocks,
    serialise_tool_result,
    tool_defs_for_anthropic,
)
from chat.turn_input import ChatTurnInput

logger = logging.getLogger(__name__)

# Soft cap on tool-use iterations within a single /chat/message stream.
# An "iteration" is one round-trip to Anthropic that may include tool calls.
# We lowered this from 60 to keep runaway agents from hanging the Vercel
# proxy (which times out the SSE connection and surfaces as "Failed to fetch"
# in the browser). When hit, we emit a user-facing fallback message and a
# `done` event with stop_reason="iteration_cap" instead of cutting off mid-stream.
# NOTE: this is per-request. The /chat/message "continue" flow re-enters this
# loop with a fresh budget, but the prior tool transcript is already in the
# conversation so the model resumes mid-thought rather than restarting.
MAX_ITERATIONS = 30
MAX_TOOL_RESULT_CHARS = 15000


def _serialise_tool_result_for_model(tool_result: Any) -> str:
    """Return bounded, valid JSON for a tool result sent back to the model."""

    result_json = serialise_tool_result(tool_result)
    if len(result_json) <= MAX_TOOL_RESULT_CHARS:
        return result_json

    if isinstance(tool_result, dict):
        data_key = next(
            (
                key
                for key, value in tool_result.items()
                if isinstance(value, list) and len(value) > 5
            ),
            None,
        )
        if data_key:
            from engine.serialization import explore_tabular_data

            data_array = tool_result[data_key]
            processed = {
                **{key: value for key, value in tool_result.items() if key != data_key},
                "note": (
                    f"Large '{data_key}' array ({len(data_array)} rows) - "
                    "showing first 20 with column metadata"
                ),
                "exploration": explore_tabular_data(data_array),
                data_key: data_array[:20],
            }
            result_json = serialise_tool_result(processed)
            if len(result_json) <= MAX_TOOL_RESULT_CHARS:
                return result_json

        fallback = {
            "status": (
                "error"
                if tool_result.get("error")
                else tool_result.get("status", "success")
            ),
            "result_id": tool_result.get("result_id"),
            "note": (
                "Tool result exceeded the model context limit. Use a narrower "
                "discovery query or request a more specific derivative output."
            ),
        }
        return serialise_tool_result(
            {key: value for key, value in fallback.items() if value is not None}
        )

    return serialise_tool_result(
        {
            "status": "truncated",
            "note": (
                "Tool result exceeded the model context limit. Use a narrower "
                "query or request a more specific output."
            ),
        }
    )


def _user_facing_error_message(session_id: str) -> str:
    """Generic text for terminal SSE `error` events.

    Raw exception strings (Anthropic SDK, httpx, Supabase) can embed internal
    URLs, file paths, and provider payloads, so they must never be streamed to
    the client. The full exception and traceback stay in the server logs; the
    session id gives users a correlation reference to quote when reporting the
    problem.
    """
    return (
        "Something went wrong while generating this response. Please try "
        f"again — if the problem persists, quote session {session_id} when "
        "reporting it."
    )


async def run_chat_turn(
    turn: ChatTurnInput,
    *,
    is_cancelled: CancellationProbe,
):
    """Run one model/tool turn and emit framework-independent events."""

    ttft_recorded = False
    terminal_stop_reason: str | None = None
    captured_exception: Exception | None = None
    captured_traceback = ""
    conversation = turn.messages.copy()
    iteration = 0
    total_input_tokens = 0
    total_output_tokens = 0
    total_cache_read_input_tokens = 0
    total_cache_creation_input_tokens = 0
    recent_tool_calls: List[str] = []
    tool_call_counts: Dict[str, int] = {}
    last_tool_error: str | None = None
    verdict = None
    route = "compute"
    model: str | None = None
    tool_context = new_tool_context(turn_id=turn.session_id)

    def usage() -> ChatUsage:
        return ChatUsage(
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            cache_creation_input_tokens=total_cache_creation_input_tokens,
            cache_read_input_tokens=total_cache_read_input_tokens,
        )

    def annotate_turn(stop_reason: str | None) -> None:
        annotate(
            model=model,
            stop_reason=stop_reason,
            iterations=iteration,
            tool_calls=sum(tool_call_counts.values()),
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            cache_read_input_tokens=total_cache_read_input_tokens,
            cache_creation_input_tokens=total_cache_creation_input_tokens,
        )

    def cancelled_event() -> TurnCancelled:
        nonlocal terminal_stop_reason
        terminal_stop_reason = "client_disconnected"
        annotate_turn(terminal_stop_reason)
        record_event(
            "chat.client_disconnected",
            session_id=turn.session_id,
            route=route,
            model=model,
            iterations=iteration,
            tool_calls=sum(tool_call_counts.values()),
        )
        return TurnCancelled(
            session_id=turn.session_id,
            model=model,
            route=route,
            usage=usage(),
        )

    @asynccontextmanager
    async def capture_turn_errors():
        nonlocal captured_exception
        nonlocal captured_traceback
        nonlocal terminal_stop_reason

        try:
            yield
        except Exception as exc:
            import traceback

            captured_exception = exc
            captured_traceback = traceback.format_exc()
            if terminal_stop_reason is None:
                terminal_stop_reason = "error"
            try:
                annotate_turn(terminal_stop_reason)
                record_error(exc, handled=True, status_code=500)
            except Exception:
                logger.exception("[CHAT] Failed to record observability for chat turn error")

    try:
        async with operation(
            "chat.turn",
            flavor="chat",
            session_id=turn.session_id,
        ), capture_turn_errors():
            annotate(session_id=turn.session_id, charts_mode=turn.charts_mode)
            if await is_cancelled():
                yield cancelled_event()
                return

            loop = asyncio.get_running_loop()
            proposal_error: str | None = None
            if is_followup(conversation):
                try:
                    verdict = await loop.run_in_executor(
                        None,
                        partial(
                            resume_gateway_proposal,
                            conversation,
                            session_id=turn.session_id,
                        ),
                    )
                except ProposalSigningError:
                    logger.warning(
                        "[GATEWAY] Session %s supplied an invalid proposal marker",
                        turn.session_id,
                    )
                    proposal_error = (
                        "I couldn’t verify the earlier reform proposal. Please "
                        "restate the policy change you want me to model."
                    )
            else:
                async with asegment(SegmentName.GATEWAY_CLASSIFY):
                    verdict = await loop.run_in_executor(
                        None, run_gateway, last_user_text(conversation)
                    )

            if verdict is not None:
                route = verdict.route

            clarification: str | None = proposal_error
            if verdict is not None and verdict.outcome == "needs_plan":
                clarification = render_clarification(verdict)
                if clarification is None:
                    logger.warning(
                        "[GATEWAY] Unrenderable reasons; failing open to compute: %s",
                        verdict.gating_reasons,
                    )
                    unresolved_reform = (
                        verdict.reform_intent is not None
                        and verdict.reform_assessment is None
                    )
                    verdict = replace(
                        verdict,
                        outcome="ready",
                        route="compute",
                        gating_reasons=[],
                    )
                    route = "compute"
                    if unresolved_reform:
                        tool_context.require_approved_reform = True
                        tool_context.approved_reform = None
                elif any(
                    reason.code == "confirm_reform"
                    for reason in verdict.gating_reasons
                ):
                    clarification = append_proposal_marker(
                        clarification,
                        proposal_payload_from_verdict(verdict),
                        session_id=turn.session_id,
                        source_prompt=last_user_text(conversation),
                    )

            if clarification is not None:
                route = "lightweight"
                if await is_cancelled():
                    yield cancelled_event()
                    return
                mark_ttft_attribute()
                ttft_recorded = True
                terminal_stop_reason = "gateway_clarification"
                annotate_turn(terminal_stop_reason)
                yield TextChunk(clarification)
                yield TurnCompleted(
                    content=clarification,
                    session_id=turn.session_id,
                    model=None,
                    route=route,
                    outcome="needs_plan",
                    stop_reason=terminal_stop_reason,
                    usage=usage(),
                )
                return

            if verdict is not None:
                if (
                    verdict.execution_plan is not None
                    and verdict.execution_plan.approved_reform is not None
                ):
                    tool_context.approved_reform = dict(
                        verdict.execution_plan.approved_reform
                    )
                    tool_context.require_approved_reform = True

            conversation = strip_proposal_markers_from_conversation(conversation)
            client = get_async_client()

            annotate(gateway_route=route)
            if verdict is not None:
                annotate(gateway_outcome=verdict.outcome, gateway_tool=verdict.tool)

            with segment(
                SegmentName.MODEL_SELECT,
                route=route,
                charts_mode=turn.charts_mode,
            ):
                model = (
                    DEFAULT_FAST_MODEL
                    if route == "lightweight"
                    else select_chat_model(
                        conversation,
                        charts_mode=turn.charts_mode,
                        gateway_verdict=verdict,
                    )
                )

            if route == "lightweight":
                tools = []
                with segment(SegmentName.SYSTEM_BUILD, route=route):
                    system_blocks = build_lightweight_system_blocks(verdict)
            else:
                with segment(SegmentName.TOOL_SCHEMA_BUILD):
                    tools = tool_defs_for_anthropic()
                gateway_plan = None
                if verdict is not None:
                    with segment(SegmentName.GATEWAY_PLAN_SERIALIZE):
                        gateway_plan = serialise_plan_for_system(verdict)
                with segment(
                    SegmentName.SYSTEM_BUILD,
                    route=route,
                    charts_mode=turn.charts_mode,
                ):
                    system_blocks = build_system_blocks(
                        charts_mode=turn.charts_mode,
                        gateway_plan=gateway_plan,
                    )
            annotate(model=model)

            logger.info(
                f"[CHAT] Session {turn.session_id}: {len(conversation)} messages"
                f"{' [CHARTS MODE]' if turn.charts_mode else ''}"
                f"{f' [GATEWAY {verdict.outcome}]' if verdict is not None else ''}"
            )

            while iteration < MAX_ITERATIONS:
                if await is_cancelled():
                    yield cancelled_event()
                    return

                iteration += 1
                async with asegment(
                    SegmentName.MODEL_ITERATION,
                    iteration=iteration,
                    model=model,
                ):
                    tool_uses = []
                    assistant_content = ""
                    last_stop_reason: str | None = None
                    max_retries = 2

                    for attempt in range(max_retries + 1):
                        try:
                            stream_kwargs: Dict[str, Any] = {
                                "model": model,
                                "max_tokens": 16000,
                                "temperature": DEFAULT_TEMPERATURE,
                                "system": system_blocks,
                                "messages": conversation,
                            }
                            if tools:
                                stream_kwargs["tools"] = tools

                            async with (
                                asegment(
                                    SegmentName.MODEL_STREAM,
                                    iteration=iteration,
                                    model=model,
                                ),
                                client.messages.stream(**stream_kwargs) as stream,
                            ):
                                announced_tools: set[str] = set()
                                async for event in stream:
                                    event_type = type(event).__name__
                                    if event_type == "RawContentBlockStartEvent":
                                        block = event.content_block
                                        if (
                                            block.type == "tool_use"
                                            and block.id not in announced_tools
                                        ):
                                            announced_tools.add(block.id)
                                            yield ToolStarted(block.name, block.id)
                                    elif event_type == "RawContentBlockDeltaEvent":
                                        delta = event.delta
                                        if delta.type == "text_delta" and delta.text:
                                            if not ttft_recorded:
                                                mark_ttft_attribute()
                                                ttft_recorded = True
                                            assistant_content += delta.text
                                            yield TextChunk(delta.text)
                                    elif event_type == "RawMessageStartEvent":
                                        event_usage = getattr(event.message, "usage", None)
                                        if event_usage:
                                            total_input_tokens += getattr(
                                                event_usage, "input_tokens", 0
                                            )
                                            total_cache_read_input_tokens += getattr(
                                                event_usage,
                                                "cache_read_input_tokens",
                                                0,
                                            )
                                            total_cache_creation_input_tokens += getattr(
                                                event_usage,
                                                "cache_creation_input_tokens",
                                                0,
                                            )
                                    elif event_type == "RawMessageDeltaEvent":
                                        event_usage = getattr(event, "usage", None)
                                        if event_usage:
                                            total_output_tokens += getattr(
                                                event_usage, "output_tokens", 0
                                            )

                                final = await stream.get_final_message()
                                last_stop_reason = getattr(final, "stop_reason", None)
                                for block in final.content:
                                    if block.type != "tool_use":
                                        continue
                                    if not tools:
                                        logger.warning(
                                            "[CHAT] Dropping unexpected tool_use with no tools "
                                            f"sent: {block.name}"
                                        )
                                        continue
                                    tool_input = (
                                        block.input if isinstance(block.input, dict) else {}
                                    )
                                    tool_use = {
                                        "id": block.id,
                                        "name": block.name,
                                        "input": tool_input,
                                    }
                                    tool_uses.append(tool_use)
                                    yield ToolUsed(
                                        tool_name=block.name,
                                        tool_id=block.id,
                                        tool_input=tool_input,
                                    )
                            break
                        except (
                            httpx.ReadError,
                            httpx.RemoteProtocolError,
                            httpx.ConnectError,
                        ) as exc:
                            logger.warning(
                                "[CHAT] Anthropic stream error "
                                f"(attempt {attempt + 1}/{max_retries + 1}): {exc}"
                            )
                            if attempt == max_retries:
                                raise
                            tool_uses = []
                            assistant_content = ""
                            await asyncio.sleep(1)

                    if tool_uses and assistant_content.strip():
                        yield ThinkingCompleted()

                    if not tool_uses:
                        terminal_stop_reason = last_stop_reason
                        annotate_turn(terminal_stop_reason)
                        yield TurnCompleted(
                            content=assistant_content,
                            session_id=turn.session_id,
                            model=model,
                            route=route,
                            outcome=verdict.outcome if verdict else None,
                            stop_reason=terminal_stop_reason,
                            usage=usage(),
                        )
                        if route == "compute" and last_stop_reason in (
                            "end_turn",
                            "stop_sequence",
                            None,
                        ):
                            last_user_message = next(
                                (
                                    message["content"]
                                    for message in reversed(turn.messages)
                                    if message.get("role") == "user"
                                ),
                                "",
                            )
                            if isinstance(last_user_message, list):
                                last_user_message = " ".join(
                                    str(block.get("text", ""))
                                    for block in last_user_message
                                    if isinstance(block, dict)
                                )
                            async with asegment(SegmentName.SUGGESTIONS):
                                suggestions = await generate_followup_suggestions(
                                    last_user_message=str(last_user_message),
                                    assistant_answer=assistant_content,
                                )
                            if suggestions:
                                yield SuggestionsGenerated(suggestions)
                        return

                    signature = ",".join(
                        sorted(
                            f"{item['name']}:{json.dumps(item['input'], sort_keys=True)}"
                            for item in tool_uses
                        )
                    )
                    recent_tool_calls.append(signature)
                    if len(recent_tool_calls) > 3:
                        recent_tool_calls.pop(0)
                        if len(set(recent_tool_calls)) == 1:
                            terminal_stop_reason = "loop_detected"
                            annotate_turn(terminal_stop_reason)
                            yield TurnFailed(
                                content=(
                                    "Agent appears to be stuck in a loop. "
                                    "Please try rephrasing your question."
                                ),
                                session_id=turn.session_id,
                                stop_reason=terminal_stop_reason,
                                usage=usage(),
                                billable=True,
                            )
                            return

                    assistant_message: Dict[str, Any] = {
                        "role": "assistant",
                        "content": [],
                    }
                    if assistant_content:
                        assistant_message["content"].append(
                            {"type": "text", "text": assistant_content}
                        )
                    for tool_use in tool_uses:
                        assistant_message["content"].append(
                            {
                                "type": "tool_use",
                                "id": tool_use["id"],
                                "name": tool_use["name"],
                                "input": tool_use["input"],
                            }
                        )
                    conversation.append(assistant_message)

                    async def execute_tool_async(tool_use):
                        loop = asyncio.get_event_loop()
                        async with asegment(
                            SegmentName.TOOL_EXECUTE,
                            iteration=iteration,
                            tool=tool_use["name"],
                        ):
                            result = await loop.run_in_executor(
                                None,
                                partial(
                                    execute_tool,
                                    tool_use["name"],
                                    tool_use["input"],
                                    context=tool_context,
                                ),
                            )
                        return tool_use, result

                    tasks = [
                        asyncio.ensure_future(execute_tool_async(tool_use))
                        for tool_use in tool_uses
                    ]
                    completed_tools = {}
                    for future in asyncio.as_completed(tasks):
                        tool_use, result = await future
                        if await is_cancelled():
                            yield cancelled_event()
                            return
                        completed_tools[tool_use["id"]] = result
                        tool_call_counts[tool_use["name"]] = (
                            tool_call_counts.get(tool_use["name"], 0) + 1
                        )
                        if isinstance(result, dict):
                            error = result.get("error") or result.get("stderr")
                            if error:
                                error_text = str(error).strip()
                                if error_text:
                                    last_tool_error = error_text.splitlines()[-1][:120]
                        status = (
                            "error"
                            if isinstance(result, dict) and result.get("error")
                            else "success"
                        )
                        yield ToolCompleted(
                            tool_name=tool_use["name"],
                            tool_id=tool_use["id"],
                            status=status,
                            output=result,
                        )

                    tool_results = []
                    for tool_use in tool_uses:
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_use["id"],
                                "content": _serialise_tool_result_for_model(
                                    completed_tools[tool_use["id"]]
                                ),
                            }
                        )
                    conversation.append({"role": "user", "content": tool_results})
            else:
                if tool_call_counts:
                    tried = "ran " + ", ".join(
                        f"`{name}` {count}×"
                        for name, count in tool_call_counts.items()
                    )
                else:
                    tried = "didn't complete any tool calls"
                error_clause = (
                    f', last attempt errored with "{last_tool_error}"'
                    if last_tool_error
                    else ""
                )
                fallback = (
                    "\n\nI'm spending more iterations than expected on this without "
                    f"converging. Here's what I tried: {tried}{error_clause}. "
                    "Could you (a) rephrase the question or (b) try a more specific "
                    "scenario?"
                )
                if len(fallback) > 600:
                    fallback = fallback[:597] + "..."
                terminal_stop_reason = "iteration_cap"
                annotate_turn(terminal_stop_reason)
                yield TextChunk(fallback)
                yield TurnCompleted(
                    content=assistant_content + fallback,
                    session_id=turn.session_id,
                    model=model,
                    route=route,
                    outcome=verdict.outcome if verdict else None,
                    stop_reason=terminal_stop_reason,
                    usage=usage(),
                )

        if captured_exception is not None:
            logger.error(
                f"[CHAT] Session {turn.session_id} exception: "
                f"{captured_exception}\n{captured_traceback}"
            )
            yield TurnFailed(
                content=_user_facing_error_message(turn.session_id),
                session_id=turn.session_id,
                stop_reason="error",
                usage=usage(),
            )
    except Exception as exc:
        import traceback

        logger.error(
            f"[CHAT] Session {turn.session_id} exception: {exc}\n{traceback.format_exc()}"
        )
        yield TurnFailed(
            content=_user_facing_error_message(turn.session_id),
            session_id=turn.session_id,
            stop_reason="error",
            usage=usage(),
        )
