"""Small typed projections for model prompts and capability input views."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from conversation_context.models import (
    ContextEntity,
    ContextFact,
    ConversationContext,
    FactRequirement,
    PendingFactResolution,
    PendingQuestionStatus,
)


class PendingQuestionProjection(BaseModel):
    """Model-safe pending question without internal persistence identifiers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    capability_id: str
    prompt: str
    requirements: tuple[FactRequirement, ...]
    status: PendingQuestionStatus


class ContextProjection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    revision: int
    entities: tuple[ContextEntity, ...]
    active_facts: tuple[ContextFact, ...]
    pending_questions: tuple[PendingQuestionProjection, ...]
    pending_fact_resolutions: tuple[PendingFactResolution, ...]
    active_scope_id: str | None


def project_context(context: ConversationContext) -> ContextProjection:
    return ContextProjection(
        revision=context.revision,
        entities=context.entities,
        active_facts=context.active_facts(),
        pending_questions=tuple(
            PendingQuestionProjection(
                capability_id=question.capability_id,
                prompt=question.prompt,
                requirements=question.requirements,
                status=question.status,
            )
            for question in context.pending_questions
        ),
        pending_fact_resolutions=context.pending_fact_resolutions,
        active_scope_id=context.focus.scope_id,
    )
