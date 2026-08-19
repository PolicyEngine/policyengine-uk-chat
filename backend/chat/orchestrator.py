"""Public entry point for the single per-message analysis coordinator."""

from __future__ import annotations

from analysis.coordinator import CoordinatorDependencies, run_analysis_turn
from billing.intents import build_billing_intent
from chat.events import TurnCompleted
from chat.suggestions import generate_followup_suggestions
from chat.turn_input import ChatTurnInput


async def run_chat_turn(turn: ChatTurnInput, *, is_cancelled):
    dependencies = CoordinatorDependencies(
        billing_intent_builder=build_billing_intent,
    )
    async for event in run_analysis_turn(
        turn,
        is_cancelled=is_cancelled,
        dependencies=dependencies,
    ):
        yield event
        if (
            isinstance(event, TurnCompleted)
            and event.outcome == "completed"
            and not event.processed_duplicate
        ):
            latest_user = next(
                (
                    message.get("content", "")
                    for message in reversed(turn.messages)
                    if message.get("role") == "user"
                ),
                "",
            )
            if isinstance(latest_user, str):
                suggestions = await generate_followup_suggestions(
                    last_user_message=latest_user,
                    assistant_answer=event.content,
                )
                if suggestions:
                    from chat.events import SuggestionsGenerated

                    yield SuggestionsGenerated(suggestions)
