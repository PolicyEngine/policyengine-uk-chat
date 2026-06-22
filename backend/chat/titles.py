"""Conversation title generation."""

from config import DEFAULT_TEMPERATURE, TITLE_MODEL, get_sync_client
from prompts import TITLE_SYSTEM

from chat.schemas import TitleRequest


def make_title(request: TitleRequest) -> dict:
    client = get_sync_client()
    content = request.first_user_message
    if request.first_assistant_message:
        content += "\n\nAssistant: " + request.first_assistant_message[:500]
    response = client.messages.create(
        model=TITLE_MODEL,
        max_tokens=32,
        temperature=DEFAULT_TEMPERATURE,
        system=TITLE_SYSTEM,
        messages=[{"role": "user", "content": content}],
    )
    return {"title": response.content[0].text.strip()}
