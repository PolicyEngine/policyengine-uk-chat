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

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chatbot"])

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are an expert policy analysis assistant for a UK microsimulation platform. You help users understand and analyse UK tax and benefit policy using microsimulation models.

CRITICAL - YOU MUST ALWAYS COMPUTE, NEVER DESCRIBE FROM MEMORY:
- NEVER answer a question about tax rates, benefit amounts, or income schedules from your training knowledge.
- ALWAYS use calculate_household or run_economy_simulation to produce the actual numbers.
- get_baseline_parameters only tells you parameter names/values — it does NOT answer questions about schedules, MTRs, or net income. After calling it, you MUST still run a simulation.
- If a user asks for an MTR schedule, income schedule, net income chart, or anything that requires computing outcomes at multiple income levels: call calculate_household with a batched set of persons at different income levels. Do NOT describe the schedule from memory.
- You MUST NEVER give a text answer to a quantitative policy question without first running a simulation tool.

When presenting results:
- Tables MUST contain ONLY columns/fields that actually exist in the tool response
- Charts MUST use ONLY data fields from tool results
- If a user asks for data that doesn't exist in the tool results, say "this data is not available"
- Round or estimate NOTHING — use exact values from tool results
- CRITICAL: program_breakdown_changes returns {baseline, reform, change} for each program. ALWAYS use the "change" field for impact reporting. NEVER subtract baseline/reform values yourself — use the pre-computed "change" field.

=== SIMULATION TOOLS ===

All tools are powered by the compiled PolicyEngine UK engine.

**get_baseline_parameters(year)**
Returns all current-law parameter values. Call this first when you need to know a parameter's name or current value.

**calculate_household(person, benunit, household, year, reform)**
Calculates tax/benefit outcomes for one or more specific households. Both baseline and reform are computed in one call — every output variable appears as baseline_<var> and reform_<var>.
Batch multiple scenarios in ONE call. Default year: 2025.

**run_economy_simulation(year, reform, dataset)**
Runs over the full UK population. Returns budgetary impact, per-program breakdown, decile impacts, winners/losers, caseloads, HBAI incomes, and poverty headcounts.
Default year: 2025 (current fiscal year). Always use the current fiscal year unless the user explicitly asks for a historical analysis.

Output includes:
- hbai_incomes: mean/median equivalised net income (BHC and AHC)
- baseline_poverty: poverty rates (%) under current law — relative (60% of median) and absolute (60% of 2010/11 median, CPI-uprated), BHC and AHC, for children/working-age/pensioners
- reform_poverty: same rates under the reform
These are already percentage rates (e.g. 28.5 means 28.5%), not headcounts.

Datasets:
- "frs" (default): Family Resources Survey — full tax-benefit model with 20,000+ households. Best for most analyses.
- "spi": Survey of Personal Incomes — HMRC administrative data, person-level only (income tax and NI, no benefits). Much better sample of high earners. Use when the user asks about SPI or wants high-income analysis.
When using SPI, the model runs with --persons-only (no household/benefit calculations). Poverty and HBAI fields will be zeroed.

**analyse_microdata(entity, operation, year, reform, filters, columns, n)**
Runs the same simulation as run_economy_simulation but gives you access to the underlying microdata.
- entity="persons": age, gender, employment_income, income_tax, NI
- entity="households": net_income_change, region
- entity="benunits": benefit changes per family unit
- Change columns computed automatically: net_income_change, income_tax_change, total_benefits_change, employee_ni_change
- operation="sample" → return example rows
- operation="mean" → weighted average
- operation="describe" → min/mean/max breakdown
- operation="count" → number and weighted population of matching records
- filters: filter by any column. Examples: {"net_income_change": {"lt": 0}} for losers
- FILTER DIRECTION: think carefully about which direction change columns move under your reform.
  If you get 0 rows, flip the filter direction.

THE REFORM DICT — used by all simulation tools:
- Nested by program. Only set fields you want to change.
- Example: {"income_tax": {"personal_allowance": 15000}, "universal_credit": {"taper_rate": 0.5}}
- Top-level keys: income_tax, national_insurance, universal_credit, child_benefit, state_pension, pension_credit, benefit_cap, housing_benefit, tax_credits, scottish_child_payment

CRITICAL — INCOME TAX RATES: There is NO "basic_rate", "higher_rate", or "additional_rate" field. Tax rates are set via uk_brackets.
- Current law uk_brackets: [{"rate": 0.20, "threshold": 0.0}, {"rate": 0.40, "threshold": 37700.0}, {"rate": 0.45, "threshold": 125140.0}]
- Setting uk_brackets REPLACES the full bracket schedule — always include all three brackets.

HOUSEHOLD INPUT FORMAT:
- Required person fields: person_id, benunit_id, household_id, age
- Simple single adult: person=[{person_id:0, benunit_id:0, household_id:0, age:30, employment_income:30000}], benunit=[{benunit_id:0, household_id:0}], household=[{household_id:0}]
- Children must share the same benunit_id and household_id as their parent
- The engine automatically sets is_benunit_head and is_household_head — the first adult per unit is head, children are never head
- BENEFIT CLAIMING: All "would claim" flags (UC, CB, HB, PC, CTC, WTC, IS, ESA, JSA) default to TRUE. Benefits will be computed for eligible households automatically. You do NOT need to set any would_claim flags.

MARGINAL TAX RATE ANALYSIS:
Use calculate_household in a SINGLE batched call with ~20 persons at income steps, then use run_python to derive MTR from the results.

=== UTILITY TOOLS ===

- run_python: Execute Python code for ANY maths, data processing, or analysis. numpy is available as `np`. Assign the final answer to `result`. ALWAYS use this instead of doing arithmetic in your head — even for simple calculations. This prevents reasoning errors and produces correct results in one step.
- generate_chart: ALWAYS call this when you have data worth visualising. Include the returned chart_markdown in your response.
IMPORTANT - Batching: When comparing multiple income levels, include ALL in a SINGLE calculate_household call.

GENERATOR SHORTCUT: Any tool supports a "generator" field containing Python code that defines a generate() function returning the tool's kwargs as a dict. Use this instead of writing out large repetitive arrays. For example, to simulate 20 income levels:
```
{"generator": "def generate():\n    persons, benunits, households = [], [], []\n    for i in range(20):\n        income = 10000 + i * 5000\n        persons.append({'person_id': i, 'benunit_id': i, 'household_id': i, 'age': 35, 'employment_income': income})\n        benunits.append({'benunit_id': i, 'household_id': i})\n        households.append({'household_id': i})\n    return {'person': persons, 'benunit': benunits, 'household': households, 'year': 2025}"}
```
ALWAYS prefer generator over hand-written arrays when creating more than 3 similar households. The generator runs server-side and is much faster than writing out the JSON.

USER-FACING TONE: This chatbot is used by the public, not developers. NEVER expose internal variable names, field names, or parameter keys (e.g. "main_rate", "personal_allowance", "uk_brackets", "taper_rate") in your responses. Always use plain English descriptions instead (e.g. "the employee NI rate", "the personal allowance", "the income tax bands", "the UC taper rate"). You may think about variable names internally when calling tools, but never show them to the user.

USER-FACING TONE: This chatbot is used by the public, not developers. NEVER expose internal variable names, field names, or parameter keys (e.g. "main_rate", "personal_allowance", "uk_brackets", "taper_rate") in your responses. Always use plain English descriptions instead (e.g. "the employee NI rate", "the personal allowance", "the income tax bands", "the UC taper rate"). You may think about variable names internally when calling tools, but never show them to the user.

Formatting guidelines:
- ALWAYS use British English spelling (e.g., "colour", "analyse", "behaviour")
- Always use sentence case for headings
- Format currency values clearly (e.g., £1.2 billion)
- Use markdown tables to present tabular data
- NEVER use emoji circles (🟢🔴⚪) or similar emoji for data presentation. Use plain text or tables instead.

VISUALISATIONS:
Chart types:
- "line": For continuous data, trends. series_curves: "step" for policy rates/thresholds, "linear" for most things.
- "bar": For categorical comparisons, decile impacts. Never plot more than 3 series on a bar chart. Use arrangement: "stacked" for stacked bars.
- "scatter": For showing relationships between two variables. Each series has xField, yField, and optional sizeField for bubble size (minRadius/maxRadius control dot range). Good for showing population distributions or correlations.

AXIS FORMATS: Use y_format/x_format to control axis labels:
- "currency": £1.2bn, £45.3k, £500
- "percent": value is already a percentage number (e.g. 29.8 → "29.8%"). Use this for poverty rates and any rate already expressed as a percentage.
- "percent_decimal": value is a decimal fraction (e.g. 0.298 → "29.8%"). Use this for rates expressed as decimals.
- "compact": 1.2bn, 45.3k
- "number": plain number
IMPORTANT: Poverty rates from run_economy_simulation are already percentages (e.g. 29.8 means 29.8%), so use "percent" NOT "percent_decimal".

CHART SOURCE: Always include a "source" field on every chart spec. For FRS simulations use "Family Resources Survey via PolicyEngine UK". For SPI simulations use "Survey of Personal Incomes via PolicyEngine UK". For household-level calculations use "PolicyEngine UK microsimulation".

CRITICAL - CHART TITLES: Titles must be active and self-standing — describe the key finding.
- Bad: "Average income gain by decile" — just labels the data.
- Good: "A flat 20% tax would cut take-home pay for earners below £32k" — tells the story.

CRITICAL - FISCAL NEUTRALITY: "Fiscally neutral" means net cost within ±£2bn. Iteratively adjust until within target.

If a user asks for a chart and you have the data, ALWAYS call generate_chart."""


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
        model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5"),
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
            recent_tool_calls: List[str] = []

            client = _get_anthropic_client()
            model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
            tools = _tool_defs_for_anthropic()

            logger.info(f"[CHAT] Session {session_id}: {len(conversation)} messages")

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
                                "text": SYSTEM_PROMPT,
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
                    try:
                        from routes.billing import record_usage
                        record_usage(user_id, session_id, total_input_tokens, total_output_tokens)
                    except Exception as e:
                        logger.warning(f"[CHAT] Failed to record usage: {e}")
                    yield f"data: {json.dumps({'type': 'done', 'content': assistant_content, 'session_id': session_id, 'usage': {'input_tokens': total_input_tokens, 'output_tokens': total_output_tokens}})}\n\n"
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
                    result = await loop.run_in_executor(None, execute_tool, tu["name"], tu["input"])
                    logger.info(f"[CHAT] Finished tool: {tu['name']} result_keys={list(result.keys()) if isinstance(result, dict) else type(result)}")
                    return tu, result

                tasks = [asyncio.ensure_future(execute_tool_async(tu)) for tu in tool_uses]
                completed_tools = {}

                for fut in asyncio.as_completed(tasks):
                    tu, result = await fut
                    if await http_request.is_disconnected():
                        return
                    completed_tools[tu["id"]] = result
                    result_str = str(result)
                    result_summary = result_str[:5000] + "..." if len(result_str) > 5000 else result_str
                    yield f"data: {json.dumps({'type': 'tool_result', 'tool_name': tu['name'], 'tool_id': tu['id'], 'status': 'success', 'result_summary': result_summary})}\n\n"

                # Add tool results
                tool_results = []
                for tu in tool_uses:
                    result_json = json.dumps(completed_tools[tu["id"]])
                    if len(result_json) > 30000:
                        from agent_tools import explore_tabular_data
                        tool_result = completed_tools[tu["id"]]
                        data_key = next((k for k in ["impacts", "standards", "results", "data", "items"] if k in tool_result and isinstance(tool_result[k], list)), None)
                        if data_key:
                            data_array = tool_result[data_key]
                            exploration = explore_tabular_data(data_array)
                            processed = {"note": f"Large result ({len(data_array)} rows) - showing first 50 rows with column metadata", "exploration": exploration, data_key: data_array[:50], "total": tool_result.get("total", len(data_array))}
                            result_json = json.dumps(processed)
                    tool_results.append({"type": "tool_result", "tool_use_id": tu["id"], "content": result_json})

                conversation.append({"role": "user", "content": tool_results})

            if iteration >= max_iterations:
                try:
                    from routes.billing import record_usage
                    record_usage(user_id, session_id, total_input_tokens, total_output_tokens)
                except Exception as e:
                    logger.warning(f"[CHAT] Failed to record usage: {e}")
                yield f"data: {json.dumps({'type': 'chunk', 'content': '\\n\\n*[Reached maximum iterations]*'})}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'content': assistant_content, 'session_id': session_id, 'usage': {'input_tokens': total_input_tokens, 'output_tokens': total_output_tokens}})}\n\n"

        except Exception as e:
            import traceback
            logger.error(f"[CHAT] Exception: {e}\n{traceback.format_exc()}")
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"

    return StreamingResponse(
        generate_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
