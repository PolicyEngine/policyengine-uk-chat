"""Request/response models for the conversations endpoints."""

from pydantic import BaseModel


class SaveConversationRequest(BaseModel):
    session_id: str
    title: str
    messages: list
    user_id: str | None = None
    user_email: str | None = None


class ConversationSummary(BaseModel):
    id: int
    session_id: str
    title: str
    created_at: str
    updated_at: str


class ConversationDetail(ConversationSummary):
    messages: list


class SharedConversationDetail(BaseModel):
    title: str
    messages: list
    created_at: str


class ReportConversationRequest(BaseModel):
    user_id: str | None = None
    note: str | None = None
    app_url: str | None = None


class ReportConversationResponse(BaseModel):
    share_token: str
    share_url: str | None = None
    issue_title: str
    issue_body: str
    issue_url: str
