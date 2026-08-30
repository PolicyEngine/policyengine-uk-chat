"""Persistence contract for the latest typed conversation context."""

from __future__ import annotations

from typing import Protocol

from conversation_context.models import ConversationContext


class ConversationContextConflict(ValueError):
    """The stored revision changed after the caller loaded it."""


class ConversationContextRepository(Protocol):
    def load(self, conversation_id: str) -> ConversationContext: ...

    def save(
        self,
        context: ConversationContext,
        *,
        expected_revision: int,
    ) -> ConversationContext: ...

    def delete(self, conversation_id: str) -> None: ...
