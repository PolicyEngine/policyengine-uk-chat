"""Private typed tools for output selection, findings, and numerical checks."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from capabilities.artifacts import AggregateDimension, AggregateValue
from tools.contracts import CallerType, Tool, ToolCallContext, ToolSpec, Visibility
from tools.typed_models import SafeToolOutput


DEFAULT_SOCIETY_OUTPUTS = (
    "budgetary_impact",
    "winners_losers",
    "decile_impacts",
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RequestedOutputIssue(StrictModel):
    request: str
    kind: str
    guidance: str


class SelectSupportedOutputsInput(StrictModel):
    requested_outputs: tuple[str, ...] = ()


class SelectSupportedOutputsOutput(StrictModel):
    output_ids: tuple[str, ...]
    issues: tuple[RequestedOutputIssue, ...] = ()


_OUTPUT_ALIASES = {
    "budgetary_impact": ("budget", "cost", "revenue", "spending", "fiscal"),
    "program_statistics": ("program", "programme", "caseload"),
    "decile_impacts": ("decile", "distribution"),
    "winners_losers": ("winner", "loser", "gain", "lose"),
    "poverty": ("poverty",),
    "inequality": ("inequality", "gini", "income share", "distribution"),
}

_DEFAULT_PROFILE_REQUEST = re.compile(
    r"^(?:(?:overall )?(?:societal|society(?: wide)?|population(?: wide)?) "
    r"(?:impact|impacts|effect|effects|result|results)|overall impact|"
    r"impact on (?:all of )?(?:society|the population))$"
)


class SelectSupportedOutputsTool(
    Tool[SelectSupportedOutputsInput, SelectSupportedOutputsOutput]
):
    spec = ToolSpec(
        identifier="select_supported_outputs",
        version="1",
        description=(
            "Map ordinary output requests to identifiers returned by the authoritative "
            "supported-output registry."
        ),
        visibility=Visibility.PRIVATE,
        allowed_callers=frozenset({CallerType.CAPABILITY}),
        input_model=SelectSupportedOutputsInput,
        output_model=SelectSupportedOutputsOutput,
        tool_dependencies=("list_supported_outputs",),
    )

    async def run(
        self,
        tool_input: SelectSupportedOutputsInput,
        context: ToolCallContext,
    ) -> SelectSupportedOutputsOutput:
        registry_result = await context.invoke_tool(
            "list_supported_outputs",
            {"scope": "derivative"},
        )
        if not isinstance(registry_result, SafeToolOutput):
            raise TypeError("Supported-output registry returned an incompatible result.")
        rows = registry_result.root.get("outputs")
        if not isinstance(rows, list):
            raise ValueError("Supported-output registry omitted its output definitions.")
        supported = {
            row["name"]
            for row in rows
            if isinstance(row, dict) and isinstance(row.get("name"), str)
        }
        selected = list(DEFAULT_SOCIETY_OUTPUTS)
        issues: list[RequestedOutputIssue] = []
        for request in tool_input.requested_outputs:
            normalized = " ".join(
                request.casefold().replace("_", " ").replace("-", " ").split()
            )
            exact = next(
                (
                    output_id
                    for output_id in supported
                    if normalized == output_id.replace("_", " ")
                ),
                None,
            )
            matches = (
                [exact]
                if exact is not None
                else [
                    output_id
                    for output_id, aliases in _OUTPUT_ALIASES.items()
                    if output_id in supported
                    and any(alias in normalized for alias in aliases)
                ]
            )
            matches = list(dict.fromkeys(matches))
            if len(matches) == 1:
                if matches[0] not in selected:
                    selected.append(matches[0])
            elif len(matches) > 1:
                issues.append(
                    RequestedOutputIssue(
                        request=request,
                        kind="ambiguous",
                        guidance="Please distinguish: " + ", ".join(matches) + ".",
                    )
                )
            elif not _DEFAULT_PROFILE_REQUEST.fullmatch(normalized):
                issues.append(
                    RequestedOutputIssue(
                        request=request,
                        kind="unsupported",
                        guidance="No registered society output matches this request.",
                    )
                )
        return SelectSupportedOutputsOutput(
            output_ids=tuple(selected),
            issues=tuple(issues),
        )


class ExtractResultFindingsInput(StrictModel):
    outputs: tuple[AggregateValue, ...]


class ResultFinding(StrictModel):
    output_id: str
    metric_id: str
    label: str
    value: float | int | None
    unit: str
    dimensions: tuple[AggregateDimension, ...] = ()


class ExtractResultFindingsOutput(StrictModel):
    findings: tuple[ResultFinding, ...]


class ExtractResultFindingsTool(
    Tool[ExtractResultFindingsInput, ExtractResultFindingsOutput]
):
    spec = ToolSpec(
        identifier="extract_result_findings",
        version="1",
        description="Project validated aggregate results into narration-safe facts.",
        visibility=Visibility.PRIVATE,
        allowed_callers=frozenset({CallerType.CAPABILITY}),
        input_model=ExtractResultFindingsInput,
        output_model=ExtractResultFindingsOutput,
    )

    async def run(
        self,
        tool_input: ExtractResultFindingsInput,
        context: ToolCallContext,
    ) -> ExtractResultFindingsOutput:
        del context
        return ExtractResultFindingsOutput(
            findings=tuple(
                ResultFinding.model_validate(output.model_dump())
                for output in tool_input.outputs
            )
        )


_NUMBER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?P<sign>[+-]?)(?P<currency>[£$€]?)(?P<number>"
    r"(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?)"
    r"(?P<suffix>\s*(?:%|percent(?:age)?(?:\s+points?)?|pp|thousand|million|"
    r"billion|trillion|bn(?![A-Za-z])|[bkmt](?![A-Za-z])))?",
    re.IGNORECASE,
)
_ORDERED_LIST_PREFIX = re.compile(r"(?m)^\s*\d+[.)]\s+")


@dataclass(frozen=True)
class _Mention:
    text: str
    value: float
    decimal_places: int
    exponent: int
    suffix: str

    def normalized(self) -> tuple[tuple[float, float], ...]:
        suffix = re.sub(r"\s+", " ", self.suffix.strip().casefold())
        scales = {
            "k": 1_000.0,
            "thousand": 1_000.0,
            "m": 1_000_000.0,
            "million": 1_000_000.0,
            "b": 1_000_000_000.0,
            "bn": 1_000_000_000.0,
            "billion": 1_000_000_000.0,
            "t": 1_000_000_000_000.0,
            "trillion": 1_000_000_000_000.0,
        }
        percent = suffix in {
            "%",
            "percent",
            "percentage",
            "percent point",
            "percent points",
            "percentage point",
            "percentage points",
            "pp",
        }
        scale = 0.01 if percent else scales.get(suffix, 1.0)
        quantum = 10.0 ** (self.exponent - self.decimal_places)
        values = [(self.value * scale, quantum * scale / 2.0)]
        if percent:
            values.append((self.value, quantum / 2.0))
        return tuple(values)


class NumericalFact(StrictModel):
    label: str
    value: float | int
    unit: str


class VerifyNumericalResponseInput(StrictModel):
    draft: str
    facts: tuple[NumericalFact, ...]


class UnsupportedNumericalClaim(StrictModel):
    text: str
    normalized_values: tuple[float, ...]


class VerifyNumericalResponseOutput(StrictModel):
    supported: bool
    unsupported_claims: tuple[UnsupportedNumericalClaim, ...]
    deterministic_fact_summary: str


class VerifyNumericalResponseTool(
    Tool[VerifyNumericalResponseInput, VerifyNumericalResponseOutput]
):
    spec = ToolSpec(
        identifier="verify_numerical_response",
        version="1",
        description=(
            "Check draft currency, percentage, sign, scale, and rounded numerical "
            "claims against validated facts."
        ),
        visibility=Visibility.PRIVATE,
        allowed_callers=frozenset({CallerType.RUNTIME, CallerType.CAPABILITY}),
        input_model=VerifyNumericalResponseInput,
        output_model=VerifyNumericalResponseOutput,
    )

    async def run(
        self,
        tool_input: VerifyNumericalResponseInput,
        context: ToolCallContext,
    ) -> VerifyNumericalResponseOutput:
        del context
        trusted = tuple(float(fact.value) for fact in tool_input.facts)
        unsupported: list[UnsupportedNumericalClaim] = []
        draft = _ORDERED_LIST_PREFIX.sub("", tool_input.draft)
        for match in _NUMBER_PATTERN.finditer(draft):
            numeric_text = match.group("number")
            value = float(numeric_text.replace(",", ""))
            if match.group("sign") == "-":
                value = -value
            mantissa, _separator, exponent_text = numeric_text.casefold().partition("e")
            mention = _Mention(
                text=match.group(0),
                value=value,
                decimal_places=(
                    len(mantissa.rsplit(".", 1)[1]) if "." in mantissa else 0
                ),
                exponent=int(exponent_text) if exponent_text else 0,
                suffix=match.group("suffix") or "",
            )
            normalized = mention.normalized()
            if not any(
                self._matches(candidate, tolerance, source)
                for candidate, tolerance in normalized
                for source in trusted
            ):
                unsupported.append(
                    UnsupportedNumericalClaim(
                        text=mention.text,
                        normalized_values=tuple(value for value, _ in normalized),
                    )
                )
        summary = "; ".join(
            f"{fact.label}: {fact.value:g} {fact.unit}" for fact in tool_input.facts
        )
        return VerifyNumericalResponseOutput(
            supported=not unsupported,
            unsupported_claims=tuple(unsupported),
            deterministic_fact_summary=summary,
        )

    @staticmethod
    def _matches(candidate: float, display_tolerance: float, trusted: float) -> bool:
        relative_cap = abs(trusted) * 0.05
        tolerance = min(display_tolerance, relative_cap) if relative_cap else display_tolerance
        return math.isclose(
            candidate,
            trusted,
            rel_tol=0.0,
            abs_tol=max(tolerance, 1e-9),
        )


def build_analysis_support_tools() -> tuple[Tool, ...]:
    return (
        SelectSupportedOutputsTool(),
        ExtractResultFindingsTool(),
        VerifyNumericalResponseTool(),
    )
