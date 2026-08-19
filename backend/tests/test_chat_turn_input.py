import uuid

import pytest

from chat.schemas import ChatRequest
from chat.turn_input import InvalidChatRequest, prepare_turn_input


def test_prepare_turn_input_deduplicates_roles_and_preserves_session():
    turn = prepare_turn_input(
        ChatRequest(
            messages=[
                {"role": "user", "content": "First"},
                {"role": "user", "content": "Second"},
                {"role": "assistant", "content": "Reply"},
            ],
            session_id="session-1",
            charts_mode=True,
        )
    )

    assert turn.messages == [
        {"role": "user", "content": "First\n\nSecond"},
        {"role": "assistant", "content": "Reply"},
    ]
    assert turn.session_id == "session-1"
    assert turn.turn_id.startswith("turn_")
    assert turn.charts_mode is True


def test_prepare_turn_input_generates_a_uuid_session():
    turn = prepare_turn_input(
        ChatRequest(messages=[{"role": "user", "content": "Hello"}])
    )

    assert str(uuid.UUID(turn.session_id)) == turn.session_id
    assert turn.turn_id.startswith("turn_")


def test_prepare_turn_input_preserves_client_turn_identifier():
    turn = prepare_turn_input(
        ChatRequest(
            messages=[{"role": "user", "content": "Hello"}],
            session_id="session-1",
            turn_id="client-turn-1",
        )
    )
    assert turn.turn_id == "client-turn-1"


def test_compatible_client_turn_identifier_is_deterministic():
    request = ChatRequest(
        messages=[{"role": "user", "content": "Hello"}],
        session_id="session-1",
    )
    assert prepare_turn_input(request).turn_id == prepare_turn_input(request).turn_id


def test_prepare_turn_input_attaches_image_to_latest_user_message():
    turn = prepare_turn_input(
        ChatRequest(
            messages=[
                {"role": "user", "content": "Earlier"},
                {"role": "assistant", "content": "Reply"},
                {"role": "user", "content": "What is this?"},
            ],
            image_base64="abc123",
            image_media_type="image/png",
        )
    )

    assert turn.messages[-1] == {
        "role": "user",
        "content": [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": "abc123",
                },
            },
            {"type": "text", "text": "What is this?"},
        ],
    }


@pytest.mark.parametrize(
    "image_base64,image_media_type,error",
    [
        ("abc", "image/svg+xml", "Unsupported image media type"),
        ("abc", None, "must be provided together"),
        (None, "image/png", "must be provided together"),
    ],
)
def test_prepare_turn_input_rejects_invalid_image_payloads(
    image_base64, image_media_type, error
):
    request = ChatRequest(
        messages=[{"role": "user", "content": "Hello"}],
        image_base64=image_base64,
        image_media_type=image_media_type,
    )

    with pytest.raises(InvalidChatRequest, match=error):
        prepare_turn_input(request)
