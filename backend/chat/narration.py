"""Free-form narration safeguards for verified quantitative facts."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

from capabilities.context import CapabilityContext
from capabilities.executor import InvocationExecutor
from tools.analysis_support import (
    NumericalFact,
    VerifyNumericalResponseOutput,
)
from tools.contracts import CallerType


Redraft = Callable[[str, VerifyNumericalResponseOutput], Awaitable[str]]


class ClarificationNarrationGuard:
    """Permit natural questions while rejecting unsupported quantitative answers."""

    _ordered_list_prefix = re.compile(r"(?m)^\s*\d+[.)]\s+")
    _substantive_number = re.compile(
        r"(?:[£$€]\s*\d|\d[\d,]*(?:\.\d+)?\s*(?:%|percent\b)|"
        r"(?<![A-Za-z0-9_])\d[\d,]*(?:\.\d+)?(?![A-Za-z0-9_]))",
        re.IGNORECASE,
    )

    def finalize(self, *, draft: str, deterministic_fallback: str | None) -> str:
        without_list_ordinals = self._ordered_list_prefix.sub("", draft)
        if self._substantive_number.search(without_list_ordinals):
            return deterministic_fallback or ""
        return draft or deterministic_fallback or ""


class NumericalNarrationVerifier:
    """Allow free-form prose, one correction, then a verified fact summary."""

    def __init__(self, executor: InvocationExecutor) -> None:
        self._executor = executor

    async def finalize(
        self,
        *,
        draft: str,
        facts: tuple[NumericalFact, ...],
        context: CapabilityContext,
        redraft: Redraft,
        deterministic_fallback: str | None = None,
        allow_redraft: bool = True,
    ) -> str:
        first = await self._verify(draft, facts, context)
        if first.supported:
            return draft
        if not allow_redraft and deterministic_fallback is not None:
            return deterministic_fallback

        corrected = await redraft(draft, first)
        second = await self._verify(corrected, facts, context)
        if second.supported:
            return corrected
        sanitized = self._without_unsupported_sentences(
            corrected,
            tuple(claim.text for claim in second.unsupported_claims),
        )
        if sanitized and sanitized != corrected:
            sanitized_result = await self._verify(sanitized, facts, context)
            if sanitized_result.supported:
                return sanitized
        return deterministic_fallback or second.deterministic_fact_summary

    @staticmethod
    def _without_unsupported_sentences(
        draft: str,
        unsupported_expressions: tuple[str, ...],
    ) -> str:
        """Remove only prose units that still contain unsupported numbers."""

        expressions = tuple(
            expression.strip()
            for expression in unsupported_expressions
            if expression.strip()
        )
        if not expressions:
            return draft
        kept_lines: list[str] = []
        for line in draft.splitlines():
            if not any(expression in line for expression in expressions):
                kept_lines.append(line)
                continue
            indentation = line[: len(line) - len(line.lstrip())]
            sentences = re.split(r"(?<=[.!?])\s+", line.strip())
            retained = [
                sentence
                for sentence in sentences
                if not any(expression in sentence for expression in expressions)
            ]
            if retained:
                kept_lines.append(indentation + " ".join(retained))
        return re.sub(r"\n{3,}", "\n\n", "\n".join(kept_lines)).strip()

    async def _verify(
        self,
        draft: str,
        facts: tuple[NumericalFact, ...],
        context: CapabilityContext,
    ) -> VerifyNumericalResponseOutput:
        output = await self._executor.invoke_tool(
            "verify_numerical_response",
            {
                "draft": draft,
                "facts": [fact.model_dump() for fact in facts],
            },
            caller=CallerType.RUNTIME,
            context=context,
        )
        if not isinstance(output, VerifyNumericalResponseOutput):
            raise TypeError("Numerical verifier returned an incompatible output.")
        return output
