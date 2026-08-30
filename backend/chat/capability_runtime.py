"""Capability-oriented chat entry point behind the existing public adapter."""

from __future__ import annotations

from collections.abc import AsyncIterator

from capabilities.application import get_capability_chat_application
from chat.events import CancellationProbe, ChatEvent
from chat.turn_input import ChatTurnInput


async def run_capability_chat_turn(
    turn: ChatTurnInput,
    *,
    is_cancelled: CancellationProbe,
) -> AsyncIterator[ChatEvent]:
    """Run one turn using the validated long-lived application composition."""

    application = get_capability_chat_application()
    async for event in application.run(turn, is_cancelled=is_cancelled):
        yield event
