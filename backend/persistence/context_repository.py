"""SQL implementation of versioned typed conversation-context persistence."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, cast

from pydantic import ValidationError
from sqlalchemy import Engine, update
from sqlmodel import Session

from conversation_context.models import ConversationContext
from conversation_context.repository import ConversationContextConflict
from conversations.models import get_engine
from persistence.capability_repository import InvalidPersistedRecord
from persistence.rows import ConversationContextRow


class SQLConversationContextRepository:
    def __init__(self, *, engine: Engine | None = None) -> None:
        self._engine = engine or get_engine()  # type: ignore[no-untyped-call]

    def load(self, conversation_id: str) -> ConversationContext:
        with Session(self._engine) as session:
            row = session.get(ConversationContextRow, conversation_id)
        if row is None:
            return ConversationContext.initial(conversation_id)
        try:
            context = ConversationContext.model_validate_json(row.payload_json)
        except ValidationError as exc:
            raise InvalidPersistedRecord(
                f"Invalid persisted conversation context for {conversation_id}."
            ) from exc
        if (
            context.conversation_id != conversation_id
            or context.schema_version != row.schema_version
            or context.revision != row.revision
        ):
            raise InvalidPersistedRecord(
                f"Conversation context envelope mismatch for {conversation_id}."
            )
        return context

    def save(
        self,
        context: ConversationContext,
        *,
        expected_revision: int,
    ) -> ConversationContext:
        if context.revision < expected_revision:
            raise ValueError("A context revision cannot move backwards.")
        now = datetime.now(timezone.utc)
        with Session(self._engine) as session:
            row = session.get(ConversationContextRow, context.conversation_id)
            if row is None:
                if expected_revision != 0:
                    raise ConversationContextConflict(
                        "Conversation context was not present at the expected revision."
                    )
                session.add(
                    ConversationContextRow(
                        conversation_id=context.conversation_id,
                        schema_version=context.schema_version,
                        revision=context.revision,
                        payload_json=context.model_dump_json(),
                        created_at=now,
                        updated_at=now,
                    )
                )
                session.commit()
                return context

            table = cast(Any, ConversationContextRow).__table__
            statement = (
                update(ConversationContextRow)
                .where(
                    table.c.conversation_id == context.conversation_id
                )
                .where(table.c.revision == expected_revision)
                .values(
                    schema_version=context.schema_version,
                    revision=context.revision,
                    payload_json=context.model_dump_json(),
                    updated_at=now,
                )
            )
            result = session.exec(statement)
            if result.rowcount != 1:
                session.rollback()
                raise ConversationContextConflict(
                    "Conversation context changed after it was loaded."
                )
            session.commit()
        return context

    def delete(self, conversation_id: str) -> None:
        with Session(self._engine) as session:
            row = session.get(ConversationContextRow, conversation_id)
            if row is not None:
                session.delete(row)
                session.commit()
