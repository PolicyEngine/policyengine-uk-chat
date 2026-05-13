"""
Experimental Claude Agent SDK runner for the chat endpoint.

This is deliberately opt-in. It lets us compare the Claude Agent SDK harness
against the existing direct Anthropic Messages loop while keeping the same
PolicyEngine backend registry and frontend SSE contract.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Dict, List

from agent_tools import execute_tool
from model_backends import get_backend

logger = logging.getLogger(__name__)


def _conversation_prompt(messages: List[dict]) -> str:
    lines = [
        "Continue this chat transcript. Respond only to the latest user message.",
        "",
    ]
    for message in messages:
        role = "User" if message.get("role") == "user" else "Assistant"
        lines.append(f"{role}: {message.get('content', '')}")
        lines.append("")
    return "\n".join(lines).strip()


def _serialise_tool_result(result: Any) -> str:
    return json.dumps(result, ensure_ascii=False, default=str)


def _usage_totals(usage: dict[str, Any] | None) -> dict[str, int]:
    if not usage:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }
    return {
        "input_tokens": int(usage.get("input_tokens", 0) or 0),
        "output_tokens": int(usage.get("output_tokens", 0) or 0),
        "cache_creation_input_tokens": int(
            usage.get("cache_creation_input_tokens", 0) or 0
        ),
        "cache_read_input_tokens": int(usage.get("cache_read_input_tokens", 0) or 0),
    }


def _event_type(event: dict[str, Any]) -> str:
    return str(event.get("type", ""))


def _content_block(event: dict[str, Any]) -> dict[str, Any]:
    block = event.get("content_block")
    return block if isinstance(block, dict) else {}


def _delta(event: dict[str, Any]) -> dict[str, Any]:
    delta = event.get("delta")
    return delta if isinstance(delta, dict) else {}


async def generate_claude_agent_sdk_stream(
    *,
    conversation: List[dict],
    system_prompt: str,
    plan_mode: bool,
    session_id: str,
    user_id: str | None,
    backend_id: str,
    model: str,
) -> AsyncIterator[str]:
    try:
        from claude_agent_sdk import ClaudeAgentOptions, create_sdk_mcp_server, query, tool
        from claude_agent_sdk.types import (
            AssistantMessage,
            ResultMessage,
            StreamEvent,
            TextBlock,
            ToolResultBlock,
            ToolUseBlock,
        )
    except ImportError as exc:
        yield f"data: {json.dumps({'type': 'error', 'content': f'Claude Agent SDK is not installed: {exc}'})}\n\n"
        return

    backend = get_backend(backend_id)

    @tool(
        "run_python",
        backend.tool_description(),
        {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": (
                        "Python code to execute. Must assign the final answer to "
                        "`result`. Use the preloaded model interface directly."
                    ),
                }
            },
            "required": ["code"],
        },
    )
    async def run_python_tool(args: dict[str, Any]) -> dict[str, Any]:
        result = execute_tool("run_python", {"code": args.get("code", "")}, backend.id)
        tool_result_queue.append(_queueable_tool_result(result))
        return {
            "content": [
                {
                    "type": "text",
                    "text": _serialise_tool_result(result),
                }
            ]
        }

    mcp_server = create_sdk_mcp_server(
        name="policyengine",
        version="0.1.0",
        tools=[run_python_tool],
    )
    allowed_tools = [] if plan_mode else ["mcp__policyengine__run_python"]
    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        model=model,
        mcp_servers={"policyengine": mcp_server},
        allowed_tools=allowed_tools,
        permission_mode="plan" if plan_mode else "default",
        include_partial_messages=True,
        max_turns=1 if plan_mode else 60,
    )

    assistant_content = ""
    usage = _usage_totals(None)
    tool_inputs: Dict[str, dict[str, Any]] = {}
    tool_names: Dict[str, str] = {}
    tool_order: List[str] = []
    tool_result_queue: List[dict[str, Any]] = []
    emitted_tool_results: set[str] = set()
    announced_tool_ids: set[str] = set()

    def _queueable_tool_result(result: dict[str, Any]) -> dict[str, Any]:
        result_json = _serialise_tool_result(result)
        return {
            "status": "error" if result.get("error") else "success",
            "result_summary": (
                result_json[:5000] + "..." if len(result_json) > 5000 else result_json
            ),
        }

    def _flush_tool_result_events() -> list[str]:
        events = []
        while tool_result_queue:
            tool_id = next(
                (candidate for candidate in tool_order if candidate not in emitted_tool_results),
                None,
            )
            if not tool_id:
                break
            emitted_tool_results.add(tool_id)
            result = tool_result_queue.pop(0)
            events.append(
                f"data: {json.dumps({'type': 'tool_result', 'tool_name': 'run_python', 'tool_id': tool_id, 'status': result['status'], 'result_summary': result['result_summary']})}\n\n"
            )
        return events

    try:
        async for message in query(
            prompt=_conversation_prompt(conversation),
            options=options,
        ):
            for event in _flush_tool_result_events():
                yield event

            if isinstance(message, StreamEvent):
                event = message.event
                event_type = _event_type(event)
                if event_type == "content_block_start":
                    block = _content_block(event)
                    if block.get("type") == "tool_use":
                        tool_id = str(block.get("id", ""))
                        tool_name = str(block.get("name", "run_python"))
                        if tool_id:
                            tool_names[tool_id] = tool_name
                            tool_inputs.setdefault(tool_id, {})
                            if tool_id not in tool_order:
                                tool_order.append(tool_id)
                        if tool_id and tool_id not in announced_tool_ids:
                            announced_tool_ids.add(tool_id)
                            yield f"data: {json.dumps({'type': 'tool_start', 'tool_name': 'run_python', 'tool_id': tool_id})}\n\n"
                elif event_type == "content_block_delta":
                    delta = _delta(event)
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        text = str(delta.get("text", ""))
                        assistant_content += text
                        yield f"data: {json.dumps({'type': 'chunk', 'content': text})}\n\n"

            elif isinstance(message, AssistantMessage):
                if message.usage:
                    usage = _usage_totals(message.usage)
                for block in message.content:
                    if isinstance(block, TextBlock):
                        if not assistant_content and block.text:
                            assistant_content += block.text
                            yield f"data: {json.dumps({'type': 'chunk', 'content': block.text})}\n\n"
                    elif isinstance(block, ToolUseBlock):
                        tool_names[block.id] = block.name
                        tool_inputs[block.id] = block.input
                        if block.id not in tool_order:
                            tool_order.append(block.id)
                        yield f"data: {json.dumps({'type': 'tool_use', 'tool_name': 'run_python', 'tool_id': block.id, 'tool_input': block.input, 'status': 'pending'})}\n\n"
                    elif isinstance(block, ToolResultBlock):
                        content = block.content
                        if isinstance(content, list):
                            content_text = "\n".join(
                                str(item.get("text", item))
                                for item in content
                                if isinstance(item, dict)
                            )
                        else:
                            content_text = str(content or "")
                        result_summary = (
                            content_text[:5000] + "..."
                            if len(content_text) > 5000
                            else content_text
                        )
                        yield f"data: {json.dumps({'type': 'tool_result', 'tool_name': 'run_python', 'tool_id': block.tool_use_id, 'status': 'error' if block.is_error else 'success', 'result_summary': result_summary})}\n\n"

            elif isinstance(message, ResultMessage):
                for event in _flush_tool_result_events():
                    yield event
                if message.usage:
                    usage = _usage_totals(message.usage)
                if message.result and not assistant_content:
                    assistant_content = message.result
                    yield f"data: {json.dumps({'type': 'chunk', 'content': message.result})}\n\n"
                billing = None
                try:
                    from routes.billing import record_usage

                    billing = record_usage(
                        user_id=user_id,
                        session_id=session_id,
                        model=model,
                        input_tokens=usage["input_tokens"],
                        output_tokens=usage["output_tokens"],
                        cache_creation_input_tokens=usage[
                            "cache_creation_input_tokens"
                        ],
                        cache_read_input_tokens=usage["cache_read_input_tokens"],
                    )
                except Exception as exc:
                    logger.warning(f"[CHAT][SDK] Failed to record usage: {exc}")

                done = {
                    "type": "done",
                    "content": assistant_content,
                    "session_id": session_id,
                    "model": model,
                    "model_backend": backend.id,
                    "agent_runner": "claude_sdk",
                    "usage": usage,
                    "cost_gbp": billing["cost_gbp"] if billing else None,
                    "balance": billing["balance"] if billing else None,
                    "sdk_session_id": message.session_id,
                    "sdk_cost_usd": message.total_cost_usd,
                }
                yield f"data: {json.dumps(done)}\n\n"
                return

        yield f"data: {json.dumps({'type': 'done', 'content': assistant_content, 'session_id': session_id, 'model': model, 'model_backend': backend.id, 'agent_runner': 'claude_sdk', 'usage': usage, 'cost_gbp': None, 'balance': None})}\n\n"
    except Exception as exc:
        logger.exception("[CHAT][SDK] Claude Agent SDK runner failed")
        yield f"data: {json.dumps({'type': 'error', 'content': str(exc)})}\n\n"
