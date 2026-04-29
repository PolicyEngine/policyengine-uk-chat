"""
Chatbot router — SSE streaming with pydantic-ai tool use.
"""

import asyncio
import json
import logging
import uuid
from typing import Any, Dict, List

import httpx

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.settings import ModelSettings

from agent_tools import execute_tool, get_tool_definitions
from model_backends import available_backends, get_backend

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chatbot"])

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
SYSTEM_PROMPT_TEMPLATE = """You are an expert policy analysis assistant for a microsimulation platform. You help users understand and analyse tax and benefit policy using reproducible Python code.

CRITICAL - ALWAYS COMPUTE WITH PYTHON:
- Never answer quantitative policy questions from memory.
- You have one execution tool: `run_python`.
- Use `run_python` for every tax, benefit, reform, schedule, poverty, decile, and distributional question.
- Every number in your answer must come directly from the Python result you just computed.

CRITICAL - START BY READING THE MODEL INSTRUCTIONS:
- At the start of a new line of analysis, use Python to inspect `capabilities()`.
- Use that to ground yourself in the available datasets, years, programmes, and caveats before you simulate.
- If the user asks about something outside the modelled scope, say so clearly instead of guessing.

{backend_prompt_context}

REPRODUCIBILITY RULES:
- Write clear Python that another developer could copy and run.
- Prefer one substantial `run_python` call over many tiny ones.
- Put the important output into `result`.
- Use `print()` only for short diagnostics.
- Do not rely on hidden reasoning for calculations when code can do the work.

DATASETS:
- Tell the user which dataset you used when it matters.
- If a dataset is unavailable in the selected backend, explain the limitation.

ANALYTICAL NOTES:
- Decile impacts are decile-level averages, not economy-wide means.
- Poverty outputs are already percentage rates, not decimal shares.
- If a result is counterintuitive, explain the mechanism briefly.
- Stay analytically neutral and use British English.

USER-FACING STYLE:
- Prefer plain English in the prose answer.
- Avoid exposing internal parameter keys unless the user wants code-level detail.
- Keep the answer grounded in what the Python run actually showed.
- Do not paste the full Python into the main answer unless the user asks; the UI will show the executed code separately.
"""


def _build_system_prompt(backend_id: str) -> str:
    backend = get_backend(backend_id)
    return SYSTEM_PROMPT_TEMPLATE.format(
        backend_prompt_context=backend.prompt_context()
    )


# ---------------------------------------------------------------------------
# Pydantic-AI agent setup
# ---------------------------------------------------------------------------

# We build the agent with tools dynamically from TOOL_DEFINITIONS
# pydantic-ai uses its own tool registration, but we'll drive it through
# our own SSE loop using the underlying model API directly for streaming.

# For now we use the AnthropicModel directly to keep full SSE control.
# pydantic-ai's streaming API is used for text + tool call events.

import os
import anthropic as anthropic_sdk

DEFAULT_FAST_MODEL = os.environ.get("ANTHROPIC_FAST_MODEL", "claude-haiku-4-5")
DEFAULT_COMPLEX_MODEL = os.environ.get("ANTHROPIC_COMPLEX_MODEL", "claude-sonnet-4-6")
TITLE_MODEL = os.environ.get("ANTHROPIC_TITLE_MODEL", DEFAULT_FAST_MODEL)
FAST_MODEL_MAX_INPUT_TOKENS = int(os.environ.get("ANTHROPIC_FAST_MODEL_MAX_INPUT_TOKENS", "120000"))


def _get_anthropic_client():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY environment variable not set")
    return anthropic_sdk.AsyncAnthropic(api_key=api_key)


def _get_sync_anthropic_client():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY environment variable not set")
    return anthropic_sdk.Anthropic(api_key=api_key)


def _tool_defs_for_anthropic(backend_id: str):
    """Convert our TOOL_DEFINITIONS to Anthropic SDK format.
    Mark the last tool with cache_control so the system prompt + all tools
    are cached across requests (prompt caching)."""
    defs = []
    tool_definitions = get_tool_definitions(backend_id)
    for i, t in enumerate(tool_definitions):
        d = {
            "name": t["name"],
            "description": t["description"],
            "input_schema": t["input_schema"],
        }
        if i == len(tool_definitions) - 1:
            d["cache_control"] = {"type": "ephemeral"}
        defs.append(d)
    return defs


def _serialise_tool_result(result: Any) -> str:
    return json.dumps(result, ensure_ascii=False, default=str)


def _estimate_message_tokens(messages: List[dict]) -> int:
    char_count = sum(len(str(block.get("content", ""))) for block in messages)
    return char_count // 4


def _select_chat_model(messages: List[dict]) -> str:
    estimated_input_tokens = _estimate_message_tokens(messages) + 5000
    if estimated_input_tokens > FAST_MODEL_MAX_INPUT_TOKENS:
        return DEFAULT_COMPLEX_MODEL
    return DEFAULT_FAST_MODEL


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    session_id: str | None = None
    user_id: str | None = None
    model_backend: str | None = None


class TitleRequest(BaseModel):
    first_user_message: str
    first_assistant_message: str | None = None


# ---------------------------------------------------------------------------
# Title endpoint
# ---------------------------------------------------------------------------

@router.post("/title")
def generate_title(request: TitleRequest):
    client = _get_sync_anthropic_client()
    content = request.first_user_message
    if request.first_assistant_message:
        content += "\n\nAssistant: " + request.first_assistant_message[:500]
    response = client.messages.create(
        model=TITLE_MODEL,
        max_tokens=32,
        system=(
            "You are titling conversations from a UK tax and benefit policy assistant. "
            "Generate a very short title (4–6 words) that accurately describes the policy question being asked. "
            "Use UK policy terminology (e.g. 'marginal tax rate' not 'MTR', 'National Insurance' not 'NI', 'Income Support' not 'IS'). "
            "Use sentence case (capitalise only the first word and proper nouns). "
            "Output only the title with no punctuation, quotes, or explanation."
        ),
        messages=[{"role": "user", "content": content}],
    )
    return {"title": response.content[0].text.strip()}


@router.get("/backends")
def list_backends():
    return {
        "default": os.environ.get("POLICYENGINE_CHAT_BACKEND", "uk_compiled"),
        "backends": available_backends(),
    }


# ---------------------------------------------------------------------------
# Chat endpoint — SSE streaming
# ---------------------------------------------------------------------------

@router.post("/message")
async def chat_message(request: ChatRequest, http_request: Request):
    # Check billing balance if user is authenticated
    user_id = request.user_id
    if user_id:
        try:
            from routes.billing import check_balance
            has_credit, _ = check_balance(user_id)
            if not has_credit:
                return JSONResponse(status_code=402, content={"error": "No credit remaining. Please top up to continue."})
        except RuntimeError:
            pass  # Supabase not configured — skip billing check

    session_id = request.session_id or str(uuid.uuid4())
    backend_id = request.model_backend or os.environ.get(
        "POLICYENGINE_CHAT_BACKEND", "uk_compiled"
    )
    try:
        backend = get_backend(backend_id)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

    messages = [{"role": msg.role, "content": msg.content} for msg in request.messages]

    # Deduplicate consecutive same-role messages
    deduplicated = []
    for msg in messages:
        if not deduplicated or deduplicated[-1]["role"] != msg["role"]:
            deduplicated.append(msg)
        else:
            deduplicated[-1]["content"] += "\n\n" + msg["content"]

    async def generate_stream():
        try:
            conversation = deduplicated.copy()
            iteration = 0
            max_iterations = 60
            total_input_tokens = 0
            total_output_tokens = 0
            total_cache_read_input_tokens = 0
            total_cache_creation_input_tokens = 0
            recent_tool_calls: List[str] = []

            client = _get_anthropic_client()
            model = _select_chat_model(conversation)
            system_prompt = _build_system_prompt(backend.id)
            tools = _tool_defs_for_anthropic(backend.id)

            logger.info(
                f"[CHAT] Session {session_id}: {len(conversation)} messages, backend={backend.id}"
            )

            while iteration < max_iterations:
                if await http_request.is_disconnected():
                    return

                iteration += 1
                tool_uses = []
                assistant_content = ""

                logger.info(f"[CHAT] Iteration {iteration}: calling Anthropic, {len(conversation)} messages")
                # Stream from Anthropic with retry on transient errors
                max_retries = 2
                for attempt in range(max_retries + 1):
                    try:
                        async with client.messages.stream(
                            model=model,
                            max_tokens=16000,
                            system=[{
                                "type": "text",
                                "text": system_prompt,
                                "cache_control": {"type": "ephemeral"},
                            }],
                            tools=tools,
                            messages=conversation,
                        ) as stream:
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
                            for block in final.content:
                                if block.type == "tool_use":
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
                    # Record token usage for billing
                    billing = None
                    try:
                        from routes.billing import record_usage
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
                    yield f"data: {json.dumps({'type': 'done', 'content': assistant_content, 'session_id': session_id, 'model': model, 'model_backend': backend.id, 'usage': {'input_tokens': total_input_tokens, 'output_tokens': total_output_tokens, 'cache_creation_input_tokens': total_cache_creation_input_tokens, 'cache_read_input_tokens': total_cache_read_input_tokens}, 'cost_gbp': billing['cost_gbp'] if billing else None, 'balance': billing['balance'] if billing else None})}\n\n"
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

                # Execute tools in parallel
                logger.info(f"[CHAT] Executing {len(tool_uses)} tools: {[t['name'] for t in tool_uses]}")

                async def execute_tool_async(tu):
                    loop = asyncio.get_event_loop()
                    logger.info(f"[CHAT] Starting tool: {tu['name']} input={tu['input']}")
                    result = await loop.run_in_executor(
                        None, execute_tool, tu["name"], tu["input"], backend.id
                    )
                    logger.info(f"[CHAT] Finished tool: {tu['name']} result_keys={list(result.keys()) if isinstance(result, dict) else type(result)}")
                    return tu, result

                tasks = [asyncio.ensure_future(execute_tool_async(tu)) for tu in tool_uses]
                completed_tools = {}

                for fut in asyncio.as_completed(tasks):
                    tu, result = await fut
                    if await http_request.is_disconnected():
                        return
                    completed_tools[tu["id"]] = result
                    result_str = _serialise_tool_result(result)
                    result_summary = result_str[:5000] + "..." if len(result_str) > 5000 else result_str
                    yield f"data: {json.dumps({'type': 'tool_result', 'tool_name': tu['name'], 'tool_id': tu['id'], 'status': 'success', 'result_summary': result_summary})}\n\n"

                # Add tool results (truncate aggressively to avoid context blowup)
                MAX_RESULT_CHARS = 15000
                tool_results = []
                for tu in tool_uses:
                    result_json = _serialise_tool_result(completed_tools[tu["id"]])
                    if len(result_json) > MAX_RESULT_CHARS:
                        from agent_tools import explore_tabular_data
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

            if iteration >= max_iterations:
                billing = None
                try:
                    from routes.billing import record_usage
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
                max_iterations_chunk = {
                    "type": "chunk",
                    "content": "\n\n*[Reached maximum iterations]*",
                }
                yield f"data: {json.dumps(max_iterations_chunk)}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'content': assistant_content, 'session_id': session_id, 'model': model, 'model_backend': backend.id, 'usage': {'input_tokens': total_input_tokens, 'output_tokens': total_output_tokens, 'cache_creation_input_tokens': total_cache_creation_input_tokens, 'cache_read_input_tokens': total_cache_read_input_tokens}, 'cost_gbp': billing['cost_gbp'] if billing else None, 'balance': billing['balance'] if billing else None})}\n\n"

        except Exception as e:
            import traceback
            logger.error(f"[CHAT] Exception: {e}\n{traceback.format_exc()}")
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"

    return StreamingResponse(
        generate_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
