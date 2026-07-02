"""Report-a-thread endpoint: build a prefilled GitHub issue from a transcript."""

import json
import os
import uuid
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException
from sqlmodel import Session

from conversations.models import ChatConversation, get_engine
from conversations.schemas import ReportConversationRequest, ReportConversationResponse

router = APIRouter(prefix="/conversations", tags=["conversations"])


def _trim_text(text: str, limit: int = 1600) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return f"{text[:limit].rstrip()}\n\n... [{omitted} characters omitted]"


def _summarise_message(message: dict, limit: int = 400) -> str:
    role = str(message.get("role", "unknown")).upper()
    content = _trim_text(str(message.get("content", "")), limit)
    events = message.get("events") or []
    tool_names = [
        event.get("data", {}).get("tool_name")
        for event in events
        if isinstance(event, dict) and event.get("type") == "tool"
    ]
    tool_names = [name for name in tool_names if name]
    summary = f"### {role}\n\n{content or '_No content saved._'}"
    if tool_names:
        summary += "\n\nTools used: " + ", ".join(tool_names[:8])
        if len(tool_names) > 8:
            summary += ", ..."
    tool_sections = []
    for event in events:
        if not isinstance(event, dict) or event.get("type") != "tool":
            continue
        data = event.get("data") or {}
        tool_name = str(data.get("tool_name") or "unknown_tool")
        tool_section = [f"#### Tool `{tool_name}`"]
        if data.get("input") is not None:
            tool_input = _trim_text(json.dumps(data["input"], indent=2, ensure_ascii=False), 2000)
            tool_section.extend(["Input:", "```json", tool_input, "```"])
        if data.get("result_summary"):
            tool_output = _trim_text(str(data["result_summary"]), 3000)
            tool_section.extend(["Output:", "```json", tool_output, "```"])
        tool_sections.append("\n".join(tool_section))
    if tool_sections:
        summary += "\n\n" + "\n\n".join(tool_sections)
    return summary


def _build_issue_body(
    row: ChatConversation,
    *,
    messages: list,
    note: str | None,
    share_url: str | None,
) -> str:
    transcript = messages[-6:]
    lines = [
        "## What looked off",
        (note or "A user reported that something in this chat thread looks incorrect or suspicious.").strip(),
        "",
        "## Thread reference",
        f"- Conversation ID: `{row.id}`",
        f"- Session ID: `{row.session_id}`",
        f"- Title: {row.title}",
        f"- Saved at: {row.updated_at.isoformat()}",
    ]
    if share_url:
        lines.append(f"- Shared thread: {share_url}")
    # Never include the reporter's email (or any other account identity) here:
    # this body is prefilled into a public GitHub issue.

    lines.extend(["", "## Transcript excerpt"])
    for message in transcript:
        lines.extend([_summarise_message(message), ""])

    lines.extend(
        [
            "## Debugging notes",
            "- Open the shared thread to inspect the full exchange.",
            "- Reproduce the last relevant user request locally if needed.",
            "- Check whether the issue is in prompting, tool use, data, or the underlying simulation engine.",
        ]
    )
    return "\n".join(lines).strip() + "\n"


@router.post("/{conversation_id}/report", response_model=ReportConversationResponse)
def report_conversation(conversation_id: int, request: ReportConversationRequest):
    engine = get_engine()
    with Session(engine) as session:
        row = session.get(ChatConversation, conversation_id)
        if not row:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if row.user_id and row.user_id != request.user_id:
            raise HTTPException(status_code=403, detail="Not your conversation")
        if not row.share_token:
            row.share_token = str(uuid.uuid4())
            session.add(row)
            session.commit()
            session.refresh(row)

    messages = json.loads(row.messages) if isinstance(row.messages, str) else row.messages
    issue_repo = os.environ.get("GITHUB_REPORT_REPO", "PolicyEngine/policyengine-uk-chat")
    base_url = (request.app_url or os.environ.get("APP_URL") or "").rstrip("/")
    share_url = f"{base_url}/s/{row.share_token}" if base_url else None
    issue_title = f"Investigate chat thread: {row.title[:90]}".strip()
    issue_body = _build_issue_body(
        row,
        messages=messages,
        note=request.note,
        share_url=share_url,
    )
    query = urlencode({"title": issue_title, "body": issue_body})
    issue_url = f"https://github.com/{issue_repo}/issues/new?{query}"

    return ReportConversationResponse(
        share_token=row.share_token,
        share_url=share_url,
        issue_title=issue_title,
        issue_body=issue_body,
        issue_url=issue_url,
    )
