"""Chat input and event projection around the analysis turn service."""

from __future__ import annotations

from collections.abc import AsyncIterator

from analysis.dependencies import AsyncCancellationProbe
from analysis.turn_service import (
    AnalysisTurnService,
    ImageTurnBlock,
    ImageTurnSource,
    TextTurnBlock,
    TurnCommand,
    TurnMessage,
    TurnProgress,
    TurnResult,
    TurnServiceDependencies,
)
from chat.events import ChatEvent
from chat.projector import ChatEventProjector
from chat.turn_input import ChatTurnInput


def _turn_message(message: dict[str, object]) -> TurnMessage:
    role = message.get("role")
    content = message.get("content")
    if not isinstance(role, str):
        raise ValueError("chat message role must be text")
    if isinstance(content, str):
        return TurnMessage(role=role, content=content)
    if not isinstance(content, list):
        raise ValueError("chat message content must be text or content blocks")

    blocks: list[TextTurnBlock | ImageTurnBlock] = []
    for block in content:
        if not isinstance(block, dict):
            raise ValueError("chat content block must be an object")
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            blocks.append(TextTurnBlock(text=block["text"]))
            continue
        source = block.get("source")
        if (
            block.get("type") == "image"
            and isinstance(source, dict)
            and isinstance(source.get("media_type"), str)
            and isinstance(source.get("data"), str)
        ):
            blocks.append(
                ImageTurnBlock(
                    source=ImageTurnSource(
                        media_type=source["media_type"],
                        data=source["data"],
                    )
                )
            )
            continue
        raise ValueError("chat content block has an unsupported shape")
    return TurnMessage(role=role, content=tuple(blocks))


def turn_command(
    turn: ChatTurnInput,
    *,
    is_cancelled: AsyncCancellationProbe,
) -> TurnCommand:
    return TurnCommand(
        messages=tuple(_turn_message(message) for message in turn.messages),
        session_id=turn.session_id,
        turn_id=turn.turn_id,
        charts_mode=turn.charts_mode,
        billing_user_id=turn.user_id,
        is_cancelled=is_cancelled,
    )


async def run_analysis_turn(
    turn: ChatTurnInput,
    *,
    is_cancelled: AsyncCancellationProbe,
    dependencies: TurnServiceDependencies,
) -> AsyncIterator[ChatEvent]:
    """Compatibility stream over typed service results and chat projection."""

    service = AnalysisTurnService(dependencies)
    command = turn_command(turn, is_cancelled=is_cancelled)
    async for result in service.run(command):
        if isinstance(result, TurnProgress):
            for event in ChatEventProjector.project_progress(
                result.execution_id,
                result.progress,
            ):
                yield event
            continue
        assert isinstance(result, TurnResult)
        events = (
            ChatEventProjector.project_finalization(result.finalization)
            if result.finalization is not None
            else ChatEventProjector.project_outcome(
                outcome=result.outcome,
                session_id=result.session_id,
                turn_id=result.turn_id,
                usage_entries=result.usage_entries,
                trace=result.trace,
            )
        )
        for event in events:
            yield event
