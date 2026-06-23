"""Chat orchestration: the SSE streaming tool loop for /chat/message.

Builds the per-turn plan (gateway), selects the model, streams from Anthropic,
runs tools in parallel, records usage, and emits SSE events. The thin route
wrapper lives in routes.py; the reusable helpers live in the sibling chat/
modules (schemas, system_blocks, model_selection, suggestions).
"""

import asyncio
import json
import logging
import uuid
from typing import Any, Dict, List

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

from config import DEFAULT_FAST_MODEL, DEFAULT_TEMPERATURE, get_async_client
from gateway import run_gateway, serialise_plan_for_system
from tools.dispatch import execute_tool

from chat.model_selection import _is_followup, _last_user_text, _select_chat_model
from chat.schemas import ChatRequest
from chat.suggestions import _generate_followup_suggestions
from chat.system_blocks import (
    _build_lightweight_system_blocks,
    _build_system_blocks,
    _serialise_tool_result,
    _tool_defs_for_anthropic,
)

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


def stream_chat(request: Request, chat_request: ChatRequest):
    # `request` is the Starlette Request (slowapi's @limiter.limit decorators
    # require the endpoint parameter named `request` to be that type); the
    # parsed body is `chat_request`.
    # Check billing balance if user is authenticated
    user_id = chat_request.user_id
    if user_id:
        try:
            from billing import check_balance
            has_credit, _ = check_balance(user_id)
            if not has_credit:
                return JSONResponse(status_code=402, content={"error": "No credit remaining. Please top up to continue."})
        except RuntimeError:
            pass  # Supabase not configured — skip billing check

    session_id = chat_request.session_id or str(uuid.uuid4())

    messages = [{"role": msg.role, "content": msg.content} for msg in chat_request.messages]

    # Deduplicate consecutive same-role messages
    deduplicated = []
    for msg in messages:
        if not deduplicated or deduplicated[-1]["role"] != msg["role"]:
            deduplicated.append(msg)
        else:
            deduplicated[-1]["content"] += "\n\n" + msg["content"]

    # If an image is attached, rewrite the final user message into Anthropic's
    # multi-block content form: [image block, text block]. The image block uses
    # the SDK's base64 source shape. Whitelist media types defensively — the
    # API rejects anything else and we'd rather fail with a clear 400 than
    # forward a bad payload.
    _ALLOWED_IMAGE_MEDIA_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
    if chat_request.image_base64 and chat_request.image_media_type:
        media_type = chat_request.image_media_type
        if media_type not in _ALLOWED_IMAGE_MEDIA_TYPES:
            return JSONResponse(status_code=400, content={"error": f"Unsupported image media type: {media_type}"})
        # Find the last user message — that's the one the image belongs to.
        for i in range(len(deduplicated) - 1, -1, -1):
            if deduplicated[i]["role"] == "user":
                text_content = deduplicated[i]["content"]
                new_content: List[Dict[str, Any]] = [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": chat_request.image_base64,
                        },
                    }
                ]
                if text_content:
                    new_content.append({"type": "text", "text": text_content})
                deduplicated[i] = {"role": "user", "content": new_content}
                break

    async def generate_stream():
        try:
            conversation = deduplicated.copy()
            iteration = 0
            max_iterations = MAX_ITERATIONS
            total_input_tokens = 0
            total_output_tokens = 0
            total_cache_read_input_tokens = 0
            total_cache_creation_input_tokens = 0
            recent_tool_calls: List[str] = []
            # Track every tool call across the turn so the cap-hit fallback can
            # tell the user what was tried. Counts only (no inputs) keeps PII out
            # of the summary and keeps it well under the 300-char target.
            tool_call_counts: Dict[str, int] = {}
            last_tool_error: str | None = None

            client = get_async_client()
            charts_mode = chat_request.charts_mode

            # Gateway pre-pass: build a structured plan and route the turn. It is
            # skipped on follow-ups (a single-message classifier can't see the
            # context they depend on, and a reply to a partial/needs_plan prompt
            # must flow to compute). A non-`ready` outcome replies on the lean
            # lightweight path; `ready` runs the full compute loop, seeded with
            # the resolved plan. Any gateway error fails safe to compute.
            verdict = None
            route = "compute"
            if not _is_followup(conversation):
                loop = asyncio.get_running_loop()
                verdict = await loop.run_in_executor(
                    None, run_gateway, _last_user_text(conversation)
                )
                route = verdict.route

            if route == "lightweight":
                model = DEFAULT_FAST_MODEL
                tools = []
                system_blocks = _build_lightweight_system_blocks(verdict)
            else:
                model = _select_chat_model(
                    conversation,
                    charts_mode=charts_mode,
                    gateway_verdict=verdict,
                )
                tools = _tool_defs_for_anthropic()
                gateway_plan = serialise_plan_for_system(verdict) if verdict is not None else None
                system_blocks = _build_system_blocks(
                    charts_mode=charts_mode, gateway_plan=gateway_plan
                )

            logger.info(
                f"[CHAT] Session {session_id}: {len(conversation)} messages"
                f"{' [CHARTS MODE]' if charts_mode else ''}"
                f"{f' [GATEWAY {verdict.outcome}]' if verdict is not None else ''}"
            )

            while iteration < max_iterations:
                if await request.is_disconnected():
                    return

                iteration += 1
                tool_uses = []
                assistant_content = ""
                last_stop_reason: str | None = None

                logger.info(f"[CHAT] Iteration {iteration}: calling Anthropic, {len(conversation)} messages")
                # Stream from Anthropic with retry on transient errors
                max_retries = 2
                for attempt in range(max_retries + 1):
                    try:
                        # A non-`ready` gateway outcome runs with no tools, so the
                        # API cannot emit tool_use blocks — making "no tool calls
                        # on the lightweight path" a code-level invariant rather
                        # than a prompt-level promise.
                        stream_kwargs: Dict[str, Any] = {
                            "model": model,
                            "max_tokens": 16000,
                            "temperature": DEFAULT_TEMPERATURE,
                            "system": system_blocks,
                            "messages": conversation,
                        }
                        if tools:
                            stream_kwargs["tools"] = tools
                        async with client.messages.stream(**stream_kwargs) as stream:
                            announced_tools: set = set()

                            async for event in stream:
                                event_type = type(event).__name__

                                if event_type == "RawContentBlockStartEvent":
                                    block = event.content_block
                                    if block.type == "tool_use" and block.id not in announced_tools:
                                        announced_tools.add(block.id)
                                        yield f"data: {json.dumps({'type': 'tool_start', 'tool_name': block.name, 'tool_id': block.id})}\n\n"

                                elif event_type == "RawContentBlockDeltaEvent":
                                    delta = event.delta
                                    if delta.type == "text_delta" and delta.text:
                                        assistant_content += delta.text
                                        yield f"data: {json.dumps({'type': 'chunk', 'content': delta.text})}\n\n"

                                elif event_type == "RawMessageStartEvent":
                                    usage = getattr(event.message, "usage", None)
                                    if usage:
                                        total_input_tokens += getattr(usage, "input_tokens", 0)
                                        cache_read = getattr(usage, "cache_read_input_tokens", 0)
                                        cache_create = getattr(usage, "cache_creation_input_tokens", 0)
                                        total_cache_read_input_tokens += cache_read
                                        total_cache_creation_input_tokens += cache_create
                                        if cache_read or cache_create:
                                            logger.info(f"[CHAT] Cache: {cache_read} read, {cache_create} creation tokens")

                                elif event_type == "RawMessageDeltaEvent":
                                    usage = getattr(event, "usage", None)
                                    if usage:
                                        total_output_tokens += getattr(usage, "output_tokens", 0)

                            # Use final message for complete, parsed tool inputs
                            final = await stream.get_final_message()
                            last_stop_reason = getattr(final, "stop_reason", None)
                            for block in final.content:
                                if block.type == "tool_use":
                                    if not tools:
                                        # Defence-in-depth: tools weren't sent (lightweight
                                        # path), so this should be unreachable. If the API
                                        # ever returns a tool_use anyway, drop it silently
                                        # rather than executing.
                                        logger.warning(f"[CHAT] Dropping unexpected tool_use with no tools sent: {block.name}")
                                        continue
                                    tool_input = block.input if isinstance(block.input, dict) else {}
                                    tool_uses.append({"id": block.id, "name": block.name, "input": tool_input})
                                    yield f"data: {json.dumps({'type': 'tool_use', 'tool_name': block.name, 'tool_id': block.id, 'tool_input': tool_input, 'status': 'pending'})}\n\n"
                        break  # success — exit retry loop
                    except (httpx.ReadError, httpx.RemoteProtocolError, httpx.ConnectError) as e:
                        logger.warning(f"[CHAT] Anthropic stream error (attempt {attempt+1}/{max_retries+1}): {e}")
                        if attempt == max_retries:
                            raise
                        tool_uses = []
                        assistant_content = ""
                        await asyncio.sleep(1)

                # If this iteration produced text + tool calls, the text was "thinking"
                if tool_uses and assistant_content.strip():
                    yield f"data: {json.dumps({'type': 'thinking_done'})}\n\n"

                if not tool_uses:
                    logger.info(
                        f"[CHAT] Session {session_id}: converged at {iteration} iterations"
                        f" stop_reason={last_stop_reason}"
                    )
                    # Record token usage for billing
                    billing = None
                    try:
                        from billing import record_usage
                        billing = record_usage(
                            user_id=user_id,
                            session_id=session_id,
                            model=model,
                            input_tokens=total_input_tokens,
                            output_tokens=total_output_tokens,
                            cache_creation_input_tokens=total_cache_creation_input_tokens,
                            cache_read_input_tokens=total_cache_read_input_tokens,
                        )
                    except Exception as e:
                        logger.warning(f"[CHAT] Failed to record usage: {e}")
                    yield f"data: {json.dumps({'type': 'done', 'content': assistant_content, 'session_id': session_id, 'model': model, 'route': route, 'outcome': verdict.outcome if verdict else None, 'stop_reason': last_stop_reason, 'usage': {'input_tokens': total_input_tokens, 'output_tokens': total_output_tokens, 'cache_creation_input_tokens': total_cache_creation_input_tokens, 'cache_read_input_tokens': total_cache_read_input_tokens}, 'cost_gbp': billing['cost_gbp'] if billing else None, 'balance': billing['balance'] if billing else None})}\n\n"
                    # Best-effort follow-up suggestions. Only on the compute path
                    # (a lightweight refusal/clarification shouldn't get chips),
                    # and only on a clean stop.
                    if route == "compute" and last_stop_reason in ("end_turn", "stop_sequence", None):
                        last_user_msg = next(
                            (m["content"] for m in reversed(deduplicated) if m.get("role") == "user"),
                            "",
                        )
                        if isinstance(last_user_msg, list):  # defensive: structured content
                            last_user_msg = " ".join(
                                str(b.get("text", "")) for b in last_user_msg if isinstance(b, dict)
                            )
                        suggestions = await _generate_followup_suggestions(
                            last_user_message=str(last_user_msg),
                            assistant_answer=assistant_content,
                        )
                        if suggestions:
                            yield f"data: {json.dumps({'type': 'suggestions', 'suggestions': suggestions})}\n\n"
                    break

                # Detect infinite loops
                sig = ",".join(sorted(f"{t['name']}:{json.dumps(t['input'], sort_keys=True)}" for t in tool_uses))
                recent_tool_calls.append(sig)
                if len(recent_tool_calls) > 3:
                    recent_tool_calls.pop(0)
                    if len(set(recent_tool_calls)) == 1:
                        yield f"data: {json.dumps({'type': 'error', 'content': 'Agent appears to be stuck in a loop. Please try rephrasing your question.'})}\n\n"
                        break

                # Build assistant message
                assistant_message: Dict[str, Any] = {"role": "assistant", "content": []}
                if assistant_content:
                    assistant_message["content"].append({"type": "text", "text": assistant_content})
                for tu in tool_uses:
                    assistant_message["content"].append({"type": "tool_use", "id": tu["id"], "name": tu["name"], "input": tu["input"]})
                conversation.append(assistant_message)

                # Execute tools in parallel and stream results as each finishes.
                # The model-facing transcript below remains deterministic because
                # it appends tool results in the original tool-call order.
                logger.info(f"[CHAT] Executing {len(tool_uses)} tools: {[t['name'] for t in tool_uses]}")

                async def execute_tool_async(tu):
                    loop = asyncio.get_event_loop()
                    logger.info(f"[CHAT] Starting tool: {tu['name']} input={tu['input']}")
                    result = await loop.run_in_executor(None, execute_tool, tu["name"], tu["input"])
                    logger.info(f"[CHAT] Finished tool: {tu['name']} result_keys={list(result.keys()) if isinstance(result, dict) else type(result)}")
                    return tu, result

                tasks = [asyncio.ensure_future(execute_tool_async(tu)) for tu in tool_uses]
                completed_tools = {}

                for fut in asyncio.as_completed(tasks):
                    tu, result = await fut
                    if await request.is_disconnected():
                        return
                    completed_tools[tu["id"]] = result
                    tool_call_counts[tu["name"]] = tool_call_counts.get(tu["name"], 0) + 1
                    # Capture the most recent tool error so the cap-hit fallback
                    # can hint at what the agent was struggling with.
                    if isinstance(result, dict):
                        err = result.get("error") or result.get("stderr")
                        if err:
                            err_str = str(err).strip().splitlines()[-1] if str(err).strip() else ""
                            if err_str:
                                last_tool_error = err_str[:120]
                    result_str = _serialise_tool_result(result)
                    result_summary = result_str[:5000] + "..." if len(result_str) > 5000 else result_str
                    yield f"data: {json.dumps({'type': 'tool_result', 'tool_name': tu['name'], 'tool_id': tu['id'], 'status': 'success', 'result_summary': result_summary})}\n\n"

                # Add tool results (truncate aggressively to avoid context blowup)
                MAX_RESULT_CHARS = 15000
                tool_results = []
                for tu in tool_uses:
                    result_json = _serialise_tool_result(completed_tools[tu["id"]])
                    if len(result_json) > MAX_RESULT_CHARS:
                        from tools.dispatch import explore_tabular_data
                        tool_result = completed_tools[tu["id"]]
                        # Try to find and summarise large arrays
                        data_key = next((k for k in tool_result if isinstance(tool_result.get(k), list) and len(tool_result[k]) > 5), None)
                        if data_key:
                            data_array = tool_result[data_key]
                            exploration = explore_tabular_data(data_array)
                            remaining = {k: v for k, v in tool_result.items() if k != data_key}
                            processed = {**remaining, "note": f"Large '{data_key}' array ({len(data_array)} rows) - showing first 20 with column metadata", "exploration": exploration, data_key: data_array[:20]}
                            result_json = _serialise_tool_result(processed)
                        # Hard cap: if still too large, truncate the JSON string
                        if len(result_json) > MAX_RESULT_CHARS:
                            result_json = result_json[:MAX_RESULT_CHARS] + '..."}'
                    tool_results.append({"type": "tool_result", "tool_use_id": tu["id"], "content": result_json})

                conversation.append({"role": "user", "content": tool_results})

            else:
                # `while…else`: only runs when the loop exhausts max_iterations
                # *without* breaking. The convergence and infinite-loop branches
                # both break (and emit their own `done`/`error`), so a turn that
                # finishes on the cap iteration won't also trip this fallback.
                logger.info(
                    f"[CHAT] Session {session_id}: iteration cap hit at {iteration} iterations"
                    f" — tool_counts={tool_call_counts}"
                    f"{f' last_error={last_tool_error!r}' if last_tool_error else ''}"
                )
                # Build a short summary of what was tried. Kept under ~300 chars
                # so it reads as a sentence, not a transcript.
                if tool_call_counts:
                    parts = [f"`{name}` {count}×" for name, count in tool_call_counts.items()]
                    tried_clause = "ran " + ", ".join(parts)
                else:
                    tried_clause = "didn't complete any tool calls"
                error_clause = (
                    f", last attempt errored with \"{last_tool_error}\""
                    if last_tool_error else ""
                )
                fallback_message = (
                    "\n\nI'm spending more iterations than expected on this without converging. "
                    f"Here's what I tried: {tried_clause}{error_clause}. "
                    "Could you (a) rephrase the question or (b) try a more specific scenario?"
                )
                # Hard cap defensively in case tool names balloon the string.
                if len(fallback_message) > 600:
                    fallback_message = fallback_message[:597] + "..."

                billing = None
                try:
                    from billing import record_usage
                    billing = record_usage(
                        user_id=user_id,
                        session_id=session_id,
                        model=model,
                        input_tokens=total_input_tokens,
                        output_tokens=total_output_tokens,
                        cache_creation_input_tokens=total_cache_creation_input_tokens,
                        cache_read_input_tokens=total_cache_read_input_tokens,
                    )
                except Exception as e:
                    logger.warning(f"[CHAT] Failed to record usage: {e}")
                yield f"data: {json.dumps({'type': 'chunk', 'content': fallback_message})}\n\n"
                final_content = assistant_content + fallback_message
                yield f"data: {json.dumps({'type': 'done', 'content': final_content, 'session_id': session_id, 'model': model, 'route': route, 'outcome': verdict.outcome if verdict else None, 'stop_reason': 'iteration_cap', 'usage': {'input_tokens': total_input_tokens, 'output_tokens': total_output_tokens, 'cache_creation_input_tokens': total_cache_creation_input_tokens, 'cache_read_input_tokens': total_cache_read_input_tokens}, 'cost_gbp': billing['cost_gbp'] if billing else None, 'balance': billing['balance'] if billing else None})}\n\n"

        except Exception as e:
            import traceback
            logger.error(f"[CHAT] Exception: {e}\n{traceback.format_exc()}")
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"

    return StreamingResponse(
        generate_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
