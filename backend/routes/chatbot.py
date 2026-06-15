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

from agent_tools import execute_tool, TOOL_DEFINITIONS
from prompts import (
    CHARTS_MODE_DIRECTIVE,
    PLAN_MODE_DIRECTIVE,
    SUGGESTION_SYSTEM,
    SYSTEM_PROMPT,
    TITLE_SYSTEM,
)
from rate_limit import limiter, chat_key_func, CHAT_USER_LIMIT, CHAT_IP_LIMIT

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chatbot"])


# ---------------------------------------------------------------------------
# Pydantic-AI agent setup
# ---------------------------------------------------------------------------

# We build the agent with tools dynamically from TOOL_DEFINITIONS
# pydantic-ai uses its own tool registration, but we'll drive it through
# our own SSE loop using the underlying model API directly for streaming.

# For now we use the AnthropicModel directly to keep full SSE control.
# pydantic-ai's streaming API is used for text + tool call events.

import os
from pathlib import Path
import anthropic as anthropic_sdk

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

DEFAULT_FAST_MODEL = os.environ.get("ANTHROPIC_FAST_MODEL", "claude-haiku-4-5")
DEFAULT_COMPLEX_MODEL = os.environ.get("ANTHROPIC_COMPLEX_MODEL", "claude-sonnet-4-6")
TITLE_MODEL = os.environ.get("ANTHROPIC_TITLE_MODEL", DEFAULT_FAST_MODEL)
# Follow-up suggestion chips run on the same fast model — cheap, latency-tolerant.
SUGGESTION_MODEL = os.environ.get("ANTHROPIC_SUGGESTION_MODEL", DEFAULT_FAST_MODEL)
SUGGESTION_TIMEOUT_SECS = float(os.environ.get("ANTHROPIC_SUGGESTION_TIMEOUT_SECS", "5"))
FAST_MODEL_MAX_INPUT_TOKENS = int(os.environ.get("ANTHROPIC_FAST_MODEL_MAX_INPUT_TOKENS", "120000"))
CHAT_TEMPERATURE = float(os.environ.get("ANTHROPIC_CHAT_TEMPERATURE", "0"))

# Topic gate — short-circuits requests that are clearly off-topic before they
# hit the main loop. Opt-in via env so rollout can be staged.
TOPIC_GATE_ENABLED = os.environ.get("POLICYENGINE_CHAT_TOPIC_GATE_ENABLED", "false").lower() == "true"
TOPIC_GATE_MODEL = os.environ.get("POLICYENGINE_CHAT_TOPIC_GATE_MODEL", DEFAULT_FAST_MODEL)

_REFERENCE_PATH = Path(__file__).resolve().parent.parent / "reference.md"
try:
    REFERENCE_DOC = _REFERENCE_PATH.read_text()
    logger.info(f"[CHAT] Loaded reference.md ({len(REFERENCE_DOC)} chars)")
except FileNotFoundError:
    REFERENCE_DOC = ""
    logger.warning("[CHAT] reference.md not found — run scripts/build_reference.py")


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


def _tool_defs_for_anthropic():
    """Convert our TOOL_DEFINITIONS to Anthropic SDK format.
    Mark the last tool with cache_control so the system prompt + all tools
    are cached across requests (prompt caching)."""
    defs = []
    for i, t in enumerate(TOOL_DEFINITIONS):
        d = {
            "name": t["name"],
            "description": t["description"],
            "input_schema": t["input_schema"],
        }
        if i == len(TOOL_DEFINITIONS) - 1:
            d["cache_control"] = {"type": "ephemeral"}
        defs.append(d)
    return defs


def _serialise_tool_result(result: Any) -> str:
    return json.dumps(result, ensure_ascii=False, default=str)


def _estimate_message_tokens(messages: List[dict]) -> int:
    char_count = sum(len(str(block.get("content", ""))) for block in messages)
    return char_count // 4


def _select_chat_model(messages: List[dict]) -> str:
    estimated_input_tokens = (
        _estimate_message_tokens(messages)
        + len(SYSTEM_PROMPT) // 4
        + len(REFERENCE_DOC) // 4
    )
    if estimated_input_tokens > FAST_MODEL_MAX_INPUT_TOKENS:
        return DEFAULT_COMPLEX_MODEL
    return DEFAULT_FAST_MODEL


def _build_system_blocks(plan_mode: bool = False, charts_mode: bool = False) -> List[dict]:
    """System prompt + cached library reference + optional per-turn directives.

    The system prompt and reference are each marked with cache_control so they
    persist across requests. Per-turn directives (plan mode, charts mode) are
    appended AFTER both cache breakpoints so toggling them never invalidates
    cached blocks.
    """
    blocks: List[dict] = [{
        "type": "text",
        "text": SYSTEM_PROMPT,
        "cache_control": {"type": "ephemeral"},
    }]
    if REFERENCE_DOC:
        blocks.append({
            "type": "text",
            "text": REFERENCE_DOC,
            "cache_control": {"type": "ephemeral"},
        })
    if plan_mode:
        blocks.append({"type": "text", "text": PLAN_MODE_DIRECTIVE})
    if charts_mode:
        blocks.append({"type": "text", "text": CHARTS_MODE_DIRECTIVE})
    return blocks


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
    plan_mode: bool = False
    charts_mode: bool = False
    # Optional image attached to the latest user message. Sent as raw base64
    # (no `data:image/...;base64,` prefix) plus a media type like `image/png`.
    # When present, the backend converts the latest user message into a
    # multi-block content list with an Anthropic vision image block + the
    # original text block before calling the Messages API.
    image_base64: str | None = None
    image_media_type: str | None = None


class TitleRequest(BaseModel):
    first_user_message: str
    first_assistant_message: str | None = None


# ---------------------------------------------------------------------------
# Follow-up suggestion chips
# ---------------------------------------------------------------------------

async def _generate_followup_suggestions(
    last_user_message: str,
    assistant_answer: str,
) -> List[str]:
    """Best-effort follow-up question generation.

    Returns up to 3 short follow-up strings. ANY failure (timeout, API error,
    malformed JSON, empty result) returns []. Never raises — callers must be
    able to drop the suggestions silently without surfacing an error.
    """
    if not assistant_answer.strip():
        return []
    try:
        client = _get_anthropic_client()
        # Trim to keep input small: the helper sees the last user turn and the
        # assistant answer, both capped. Long tool transcripts aren't needed —
        # the answer text is what the user is reading.
        user_block = (
            "Latest user question:\n"
            + last_user_message.strip()[:1500]
            + "\n\nAssistant answer:\n"
            + assistant_answer.strip()[:4000]
        )
        response = await asyncio.wait_for(
            client.messages.create(
                model=SUGGESTION_MODEL,
                max_tokens=200,
                system=SUGGESTION_SYSTEM,
                messages=[{"role": "user", "content": user_block}],
            ),
            timeout=SUGGESTION_TIMEOUT_SECS,
        )
        text_parts = [b.text for b in response.content if getattr(b, "type", None) == "text"]
        raw = "".join(text_parts).strip()
        if not raw:
            return []
        # Strip optional code fences just in case the model ignored instructions.
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        # Allow either {"suggestions":[...]} or a bare JSON list.
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            items = parsed.get("suggestions") or parsed.get("questions") or []
        elif isinstance(parsed, list):
            items = parsed
        else:
            return []
        cleaned: List[str] = []
        for item in items:
            if not isinstance(item, str):
                continue
            s = item.strip().strip('"').strip()
            if not s:
                continue
            # Hard cap length and dedupe.
            if len(s) > 120:
                s = s[:117].rstrip() + "..."
            if s not in cleaned:
                cleaned.append(s)
            if len(cleaned) == 3:
                break
        return cleaned
    except Exception as e:  # noqa: BLE001 — best-effort, swallow everything
        logger.info(f"[CHAT] Follow-up suggestion generation failed (silent): {e}")
        return []


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
        system=TITLE_SYSTEM,
        messages=[{"role": "user", "content": content}],
    )
    return {"title": response.content[0].text.strip()}


# ---------------------------------------------------------------------------
# Topic gate
# ---------------------------------------------------------------------------

# Calibration for the boundary cases:
#   - "Capital of France?"                          → no (reject)
#   - "What did the chancellor say yesterday?"      → no (reject — news, not policy)
#   - "How will the PA reform affect inflation?"    → yes (let through; the main
#     loop's scope-refusal then explains microsim-vs-macro)
#   - "What's the EITC?" / "How does UC taper?"     → yes (factual policy)
#
# Failure mode preference: false negatives (rejecting on-topic) are worse than
# false positives (accepting off-topic). The latter wastes a few cents; the
# former breaks the product. So the prompt biases toward letting things
# through, and any classifier error short-circuits to "yes".
_TOPIC_GATE_SYSTEM = """You are a strict classifier deciding whether to forward a user's question to a UK tax-and-benefit policy assistant.

Reply with exactly one token: "yes" or "no".

Reply "yes" when the question is, or could plausibly be, about:
- UK or US tax, benefits, social-security, or public-finance policy
- Specific programmes (Universal Credit, EITC, CTC, SNAP, NHS, state pension, etc.)
- Household-level financial situations the assistant could simulate
- Reforms, hypothetical policy changes, distributional or budgetary effects
- Whether something is in scope for a microsimulation model (the assistant will explain limitations)
- Follow-ups, clarifications, or chit-chat that names a policy topic

Reply "no" only when the question is unambiguously NOT about policy — e.g.:
- General knowledge (capitals, history, science, sports, weather)
- News or current events not tied to a specific policy
- Personal advice, emotional support, creative writing
- Coding help unrelated to policy modelling

When in doubt, reply "yes".
"""


def _classify_on_topic(last_user_message: str) -> bool:
    """Single Haiku classification call. Fail-open on any error."""
    if not last_user_message or not last_user_message.strip():
        return True
    try:
        client = _get_sync_anthropic_client()
        response = client.messages.create(
            model=TOPIC_GATE_MODEL,
            max_tokens=4,
            system=_TOPIC_GATE_SYSTEM,
            messages=[{"role": "user", "content": last_user_message[:2000]}],
        )
        text = response.content[0].text.strip().lower() if response.content else ""
        return not text.startswith("no")
    except Exception as e:
        logger.warning(f"[CHAT] Topic gate classification failed; failing open: {e}")
        return True


_OFF_TOPIC_REFUSAL = (
    "I'm built for UK tax and benefit policy questions — things like reform "
    "impacts, eligibility, programme parameters, or distributional effects. "
    "I can't help with this one. If you'd like, ask me about a UK policy or "
    "household situation and I'll run the numbers."
)


def _off_topic_refusal_stream(session_id: str, model: str):
    """Emit an SSE stream the frontend renders identically to a normal answer."""
    async def gen():
        yield f"data: {json.dumps({'type': 'chunk', 'content': _OFF_TOPIC_REFUSAL})}\n\n"
        yield f"data: {json.dumps({'type': 'done', 'content': _OFF_TOPIC_REFUSAL, 'session_id': session_id, 'model': model, 'stop_reason': 'topic_gate', 'usage': {'input_tokens': 0, 'output_tokens': 0, 'cache_creation_input_tokens': 0, 'cache_read_input_tokens': 0}, 'cost_gbp': None, 'balance': None, 'refused_by_topic_gate': True})}\n\n"
    return gen


# ---------------------------------------------------------------------------
# Chat endpoint — SSE streaming
# ---------------------------------------------------------------------------

@router.post("/message")
@limiter.limit(CHAT_USER_LIMIT, key_func=chat_key_func)
@limiter.limit(CHAT_IP_LIMIT)
async def chat_message(request: Request, chat_request: ChatRequest):
    # `request` is the Starlette Request (slowapi's @limiter.limit decorators
    # require the endpoint parameter named `request` to be that type); the
    # parsed body is `chat_request`.
    # Check billing balance if user is authenticated
    user_id = chat_request.user_id
    if user_id:
        try:
            from routes.billing import check_balance
            has_credit, _ = check_balance(user_id)
            if not has_credit:
                return JSONResponse(status_code=402, content={"error": "No credit remaining. Please top up to continue."})
        except RuntimeError:
            pass  # Supabase not configured — skip billing check

    session_id = chat_request.session_id or str(uuid.uuid4())

    # Topic gate — short-circuit clearly off-topic messages before we load the
    # system prompt, reference doc, or run any tools. Only checks the most
    # recent user message; mid-conversation drift is left to the main loop's
    # scope guidance. Off by default; opt in via env so rollout can be staged.
    if TOPIC_GATE_ENABLED:
        last_user = next(
            (m.content for m in reversed(chat_request.messages) if m.role == "user"),
            "",
        )
        if not _classify_on_topic(last_user):
            logger.info(f"[CHAT] Topic gate rejected message in session {session_id}")
            return StreamingResponse(
                _off_topic_refusal_stream(session_id, TOPIC_GATE_MODEL)(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

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

            client = _get_anthropic_client()
            model = _select_chat_model(conversation)
            tools = _tool_defs_for_anthropic()
            plan_mode = chat_request.plan_mode
            charts_mode = chat_request.charts_mode
            system_blocks = _build_system_blocks(plan_mode=plan_mode, charts_mode=charts_mode)

            logger.info(
                f"[CHAT] Session {session_id}: {len(conversation)} messages"
                f"{' [PLAN MODE]' if plan_mode else ''}"
                f"{' [CHARTS MODE]' if charts_mode else ''}"
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
                        # Plan mode is enforced structurally: omit tools from the
                        # request so the API cannot emit tool_use blocks. The
                        # directive in system_blocks shapes the response; this
                        # makes "no tool calls in plan mode" a code-level invariant
                        # rather than a prompt-level promise.
                        stream_kwargs: Dict[str, Any] = {
                            "model": model,
                            "max_tokens": 16000,
                            "temperature": CHAT_TEMPERATURE,
                            "system": system_blocks,
                            "messages": conversation,
                        }
                        if not plan_mode:
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
                                    if plan_mode:
                                        # Defence-in-depth: tools weren't sent, so this
                                        # path should be unreachable. If the API ever
                                        # returns a tool_use anyway, drop it silently
                                        # rather than executing — plan mode guarantees
                                        # no tool execution.
                                        logger.warning(f"[CHAT] Dropping unexpected tool_use in plan mode: {block.name}")
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
                    yield f"data: {json.dumps({'type': 'done', 'content': assistant_content, 'session_id': session_id, 'model': model, 'stop_reason': last_stop_reason, 'usage': {'input_tokens': total_input_tokens, 'output_tokens': total_output_tokens, 'cache_creation_input_tokens': total_cache_creation_input_tokens, 'cache_read_input_tokens': total_cache_read_input_tokens}, 'cost_gbp': billing['cost_gbp'] if billing else None, 'balance': billing['balance'] if billing else None})}\n\n"
                    # Best-effort follow-up suggestions. Plan-mode answers are
                    # already clarifying questions, so don't suggest more on top.
                    # Truncated/error stops are skipped because the answer
                    # isn't really finished from the user's perspective.
                    if not plan_mode and last_stop_reason in ("end_turn", "stop_sequence", None):
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
                    "Could you (a) rephrase the question, (b) enable Plan mode so I can ask "
                    "clarifying questions first, or (c) try a more specific scenario?"
                )
                # Hard cap defensively in case tool names balloon the string.
                if len(fallback_message) > 600:
                    fallback_message = fallback_message[:597] + "..."

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
                yield f"data: {json.dumps({'type': 'chunk', 'content': fallback_message})}\n\n"
                final_content = assistant_content + fallback_message
                yield f"data: {json.dumps({'type': 'done', 'content': final_content, 'session_id': session_id, 'model': model, 'stop_reason': 'iteration_cap', 'usage': {'input_tokens': total_input_tokens, 'output_tokens': total_output_tokens, 'cache_creation_input_tokens': total_cache_creation_input_tokens, 'cache_read_input_tokens': total_cache_read_input_tokens}, 'cost_gbp': billing['cost_gbp'] if billing else None, 'balance': billing['balance'] if billing else None})}\n\n"

        except Exception as e:
            import traceback
            logger.error(f"[CHAT] Exception: {e}\n{traceback.format_exc()}")
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"

    return StreamingResponse(
        generate_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
