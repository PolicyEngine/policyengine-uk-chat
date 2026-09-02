"""Catalogue-constrained reform resolution and verified scenario creation."""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from importlib.metadata import PackageNotFoundError, version
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from capabilities.artifacts import (
    ArtifactProvenance,
    PolicyChange,
    PolicyScenarioRef,
    Scalar,
)
from capabilities.contracts import (
    ArtifactContract,
    Capability,
    CapabilitySpec,
    Completed,
    Failed,
    NeedsInput,
    Unsupported,
)
from capabilities.input_resolution import InputSource, resolve_policy_year
from config import DEFAULT_FAST_MODEL, DEFAULT_TEMPERATURE, get_async_client
from tools.contracts import CallerType, Tool, ToolCallContext, ToolSpec, Visibility
from tools.typed_models import SafeToolOutput


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReformResolutionKind(str, Enum):
    RESOLVED = "resolved"
    NEEDS_CLARIFICATION = "needs_clarification"
    NO_REFORM = "no_reform"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


class ReformMeaning(StrictModel):
    parameter_path: str = Field(
        description=(
            "Exact parameter path selected from the supplied catalogue candidates."
        )
    )
    operation: Literal["set", "increase", "decrease", "abolish"]
    value: Scalar
    unit: str | None
    effective_date: str | None
    population: str
    jurisdiction: str


class ResolverUsage(StrictModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


class ReformResolutionDecision(StrictModel):
    outcome: ReformResolutionKind
    summary: str
    clarification: str | None = None
    reform: dict[str, Scalar] = Field(default_factory=dict)
    meaning: ReformMeaning | None = None
    usage: ResolverUsage = Field(default_factory=ResolverUsage)


class ResolveReformInput(StrictModel):
    instruction: str
    year: int
    existing_scenario_id: str | None = None


class ReformCatalogueBinding(StrictModel):
    parameter_path: str
    label: str


class ResolveReformOutput(StrictModel):
    outcome: ReformResolutionKind
    year: int
    summary: str
    clarification: str | None = None
    changes: tuple[PolicyChange, ...] = ()
    bindings: tuple[ReformCatalogueBinding, ...] = ()
    catalogue_version: str
    calculation_engine_version: str


class ReformCandidateResolver(Protocol):
    async def resolve(
        self,
        *,
        instruction: str,
        year: int,
        candidates: tuple[dict[str, JsonValue], ...],
    ) -> ReformResolutionDecision: ...

    async def correct_representation(
        self,
        *,
        instruction: str,
        year: int,
        candidates: tuple[dict[str, JsonValue], ...],
        previous: ReformResolutionDecision,
        validation_errors: tuple[str, ...],
    ) -> ReformResolutionDecision: ...


class AnthropicReformCandidateResolver:
    async def resolve(self, *, instruction, year, candidates):
        return await self._call(
            instruction=instruction,
            year=year,
            candidates=candidates,
            correction=None,
        )

    async def correct_representation(
        self,
        *,
        instruction,
        year,
        candidates,
        previous,
        validation_errors,
    ):
        return await self._call(
            instruction=instruction,
            year=year,
            candidates=candidates,
            correction={
                "previous": previous.model_dump(mode="json", exclude={"usage"}),
                "validation_errors": validation_errors,
            },
        )

    async def _call(self, *, instruction, year, candidates, correction):
        client = get_async_client()
        tool = {
            "name": "submit_reform_resolution",
            "description": "Return one bounded reform-resolution outcome.",
            "input_schema": self._resolution_schema(candidates),
        }
        response = await client.messages.create(
            model=DEFAULT_FAST_MODEL,
            max_tokens=2_000,
            temperature=DEFAULT_TEMPERATURE,
            system=(
                "Resolve ordinary UK policy wording only against the supplied catalogue "
                "entries. A resolved outcome must use only supplied parameter paths and "
                "must set meaning.parameter_path to the exact selected catalogue path, "
                "then state operation, value, unit, effective date, population, and "
                "jurisdiction without inventing consequential meaning. Never put a "
                "friendly label in meaning.parameter_path. The reform field must be a "
                "flat JSON object whose key is that same exact parameter path and whose "
                "value is the final scalar parameter value; never put path, operation, "
                "from, or to fields inside reform. Return "
                "needs_clarification for semantic ambiguity and unsupported when the "
                "engine cannot represent the instruction. On a correction request, fix "
                "only serialization or type shape and preserve all ReformMeaning fields."
            ),
            messages=[
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "instruction": instruction,
                            "year": year,
                            "catalogue_candidates": candidates,
                            "representation_correction": correction,
                        },
                        default=str,
                    ),
                }
            ],
            tools=[tool],
            tool_choice={"type": "tool", "name": "submit_reform_resolution"},
        )
        block = next(
            (
                item
                for item in response.content
                if getattr(item, "type", None) == "tool_use"
                and getattr(item, "name", None) == "submit_reform_resolution"
            ),
            None,
        )
        if block is None:
            raise RuntimeError("Reform resolver did not return structured output.")
        usage = getattr(response, "usage", None)
        payload = dict(block.input)
        payload["usage"] = {
            "input_tokens": getattr(usage, "input_tokens", 0),
            "output_tokens": getattr(usage, "output_tokens", 0),
            "cache_creation_input_tokens": getattr(
                usage, "cache_creation_input_tokens", 0
            ),
            "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0),
        }
        return ReformResolutionDecision.model_validate(payload)

    @staticmethod
    def _resolution_schema(candidates):
        schema = ReformResolutionDecision.model_json_schema()
        definitions = schema.get("$defs")
        if not isinstance(definitions, dict):
            raise RuntimeError("Reform resolution schema has no definitions.")
        meaning = definitions.get("ReformMeaning")
        if not isinstance(meaning, dict):
            raise RuntimeError("Reform resolution schema has no meaning definition.")
        properties = meaning.get("properties")
        if not isinstance(properties, dict):
            raise RuntimeError("Reform meaning schema has no properties.")
        parameter_path = properties.get("parameter_path")
        if not isinstance(parameter_path, dict):
            raise RuntimeError("Reform meaning schema has no parameter path.")
        parameter_path["enum"] = [
            candidate["path"]
            for candidate in candidates
            if isinstance(candidate.get("path"), str)
        ]
        return schema


def _package_version(package: str) -> str:
    try:
        return version(package)
    except PackageNotFoundError:
        return "unavailable"


class ResolveReformTool(Tool[ResolveReformInput, ResolveReformOutput]):
    spec = ToolSpec(
        identifier="resolve_reform",
        version="1",
        description=(
            "Search the authoritative catalogue, construct one reform candidate, "
            "validate it, and return a bounded resolution or clarification."
        ),
        visibility=Visibility.PUBLIC,
        allowed_callers=frozenset({CallerType.CAPABILITY}),
        input_model=ResolveReformInput,
        output_model=ResolveReformOutput,
        tool_dependencies=(
            "list_reform_targets",
            "get_parameter",
            "validate_reform",
        ),
    )

    def __init__(self, resolver: ReformCandidateResolver) -> None:
        self._resolver = resolver

    async def run(self, tool_input: ResolveReformInput, context: ToolCallContext):
        if self._requests_baseline(tool_input.instruction):
            return self._output(
                tool_input,
                outcome=ReformResolutionKind.NO_REFORM,
                summary="Use the current-law baseline.",
            )
        search = await context.invoke_tool(
            "list_reform_targets",
            {"query": tool_input.instruction, "limit": 20},
        )
        candidates = await self._catalogue_candidates(search, tool_input.year, context)
        if not candidates:
            return self._output(
                tool_input,
                outcome=ReformResolutionKind.UNSUPPORTED,
                summary="No authoritative reform target matched the instruction.",
            )
        decision = await self._resolver.resolve(
            instruction=tool_input.instruction,
            year=tool_input.year,
            candidates=candidates,
        )
        context.record_model_usage(**decision.usage.model_dump())
        if decision.outcome is not ReformResolutionKind.RESOLVED:
            return self._from_decision(tool_input, decision)

        decision = self._normalize_candidate_mapping(decision, candidates)
        checked = self._check_meaning(decision, candidates)
        correction_used = False
        if checked is not None:
            if not self._can_correct_mapping(decision, candidates):
                return self._inconsistent_output(tool_input)
            corrected = await self._correct_representation(
                decision=decision,
                instruction=tool_input.instruction,
                year=tool_input.year,
                candidates=candidates,
                errors=(checked,),
                context=context,
            )
            correction_used = True
            if corrected is None:
                return self._inconsistent_output(tool_input)
            decision = corrected
        validation = await self._validate(decision.reform, tool_input.year, context)
        if not validation.get("valid"):
            errors = self._validation_errors(validation)
            if self._representation_only(errors) and not correction_used:
                corrected = await self._correct_representation(
                    decision=decision,
                    instruction=tool_input.instruction,
                    year=tool_input.year,
                    candidates=candidates,
                    errors=errors,
                    context=context,
                )
                correction_used = True
                if corrected is None:
                    return self._inconsistent_output(tool_input)
                decision = corrected
                validation = await self._validate(
                    decision.reform,
                    tool_input.year,
                    context,
                )
            if not validation.get("valid"):
                return self._output(
                    tool_input,
                    outcome=ReformResolutionKind.FAILED,
                    summary="The reform candidate failed deterministic validation.",
                )

        normalized = validation.get("normalized_reform")
        if not isinstance(normalized, dict) or set(normalized) != set(decision.reform):
            return self._output(
                tool_input,
                outcome=ReformResolutionKind.FAILED,
                summary="Deterministic validation changed the reform target set.",
            )
        by_path = {candidate["path"]: candidate for candidate in candidates}
        changes = tuple(
            PolicyChange(parameter_path=path, value=value)
            for path, value in normalized.items()
            if isinstance(value, (str, int, float, bool)) or value is None
        )
        if len(changes) != len(normalized):
            return self._output(
                tool_input,
                outcome=ReformResolutionKind.UNSUPPORTED,
                summary="This reform encoding is not yet transferable as a scalar change.",
            )
        return self._output(
            tool_input,
            outcome=ReformResolutionKind.RESOLVED,
            summary=decision.summary,
            changes=changes,
            bindings=tuple(
                ReformCatalogueBinding(
                    parameter_path=path,
                    label=str(by_path[path].get("label") or path),
                )
                for path in normalized
            ),
        )

    async def _catalogue_candidates(self, result, year, context):
        if not isinstance(result, SafeToolOutput):
            raise TypeError("Reform catalogue returned an incompatible output.")
        rows = result.root.get("targets")
        if not isinstance(rows, list):
            return ()
        enriched = []
        for row in rows[:20]:
            if not isinstance(row, dict) or not isinstance(row.get("path"), str):
                continue
            item = dict(row)
            detail = await context.invoke_tool(
                "get_parameter",
                {"path": row["path"], "year": year},
            )
            if isinstance(detail, SafeToolOutput) and isinstance(
                detail.root.get("parameter"), dict
            ):
                item.update(detail.root["parameter"])
            enriched.append(item)
        return tuple(enriched)

    @staticmethod
    async def _validate(reform, year, context):
        result = await context.invoke_tool(
            "validate_reform",
            {"reform": reform, "year": year},
        )
        if not isinstance(result, SafeToolOutput):
            raise TypeError("Reform validation returned an incompatible output.")
        return result.root

    @staticmethod
    def _normalize_candidate_mapping(decision, candidates):
        meaning = decision.meaning
        if meaning is None:
            return decision
        paths = {candidate["path"] for candidate in candidates}
        if (
            meaning.parameter_path not in paths
            or set(decision.reform) == {meaning.parameter_path}
        ):
            return decision
        final_value = None
        if meaning.operation == "set":
            final_value = meaning.value
        elif decision.reform.get("to") == meaning.value:
            final_value = meaning.value
        if final_value is None:
            return decision
        return decision.model_copy(
            update={"reform": {meaning.parameter_path: final_value}}
        )

    @staticmethod
    def _check_meaning(decision, candidates) -> str | None:
        if decision.meaning is None:
            return "Resolved reform output has no semantic meaning object."
        if not decision.reform:
            return "Resolved reform output has no parameter mapping."
        paths = {candidate["path"] for candidate in candidates}
        if decision.meaning.parameter_path not in paths:
            return "The semantic parameter path is absent from catalogue results."
        unknown = set(decision.reform) - paths
        if unknown:
            return "The parameter mapping contains a path absent from catalogue results."
        if decision.meaning.parameter_path not in decision.reform:
            return "The semantic parameter path does not match the parameter mapping."
        return None

    @staticmethod
    def _can_correct_mapping(decision, candidates) -> bool:
        return decision.meaning is not None and decision.meaning.parameter_path in {
            candidate["path"] for candidate in candidates
        }

    async def _correct_representation(
        self,
        *,
        decision,
        instruction,
        year,
        candidates,
        errors,
        context,
    ):
        corrected = await self._resolver.correct_representation(
            instruction=instruction,
            year=year,
            candidates=candidates,
            previous=decision,
            validation_errors=errors,
        )
        context.record_model_usage(**corrected.usage.model_dump())
        if (
            corrected.outcome is not ReformResolutionKind.RESOLVED
            or corrected.meaning != decision.meaning
        ):
            return None
        corrected = self._normalize_candidate_mapping(corrected, candidates)
        if self._check_meaning(corrected, candidates) is not None:
            return None
        return corrected

    @classmethod
    def _inconsistent_output(cls, tool_input):
        return cls._output(
            tool_input,
            outcome=ReformResolutionKind.FAILED,
            summary="The reform resolver returned inconsistent structured output.",
        )

    @staticmethod
    def _validation_errors(validation) -> tuple[str, ...]:
        errors = validation.get("errors")
        if not isinstance(errors, list):
            return ("unknown validation failure",)
        return tuple(
            str(item.get("message", item)) if isinstance(item, dict) else str(item)
            for item in errors
        )

    @staticmethod
    def _representation_only(errors: tuple[str, ...]) -> bool:
        markers = ("object", "mapping", "type", "format", "serialization", "shape")
        return bool(errors) and all(
            any(marker in error.casefold() for marker in markers) for error in errors
        )

    @staticmethod
    def _requests_baseline(instruction: str) -> bool:
        normalized = " ".join(instruction.casefold().split())
        return normalized in {
            "current law",
            "current policy",
            "no reform",
            "baseline",
            "use the baseline",
        }

    @staticmethod
    def _output(
        tool_input,
        *,
        outcome,
        summary,
        clarification=None,
        changes=(),
        bindings=(),
    ):
        return ResolveReformOutput(
            outcome=outcome,
            year=tool_input.year,
            summary=summary,
            clarification=clarification,
            changes=changes,
            bindings=bindings,
            catalogue_version=_package_version("policyengine-uk"),
            calculation_engine_version=_package_version("policyengine"),
        )

    @classmethod
    def _from_decision(cls, tool_input, decision):
        return cls._output(
            tool_input,
            outcome=decision.outcome,
            summary=decision.summary,
            clarification=decision.clarification,
        )


class PolicyReformInput(StrictModel):
    instruction: str
    year: int | None = None
    referenced_policy_scenario_id: str | None = None


class PolicyReformOutput(StrictModel):
    scenario: PolicyScenarioRef
    year_source: InputSource
    resolution_summary: str


class PolicyReformCapability(Capability[PolicyReformInput, PolicyReformOutput]):
    spec = CapabilitySpec(
        identifier="policy_reform",
        version="1",
        description=(
            "Resolve an ordinary-language UK policy change into a deterministically "
            "validated scenario reference."
        ),
        required_use=(
            "Use when another capability needs a verified reform or the user asks to "
            "construct or validate a policy change."
        ),
        visibility=Visibility.PUBLIC,
        allowed_callers=frozenset({CallerType.MODEL, CallerType.CAPABILITY}),
        input_model=PolicyReformInput,
        output_model=PolicyReformOutput,
        accepted_artifacts=(
            ArtifactContract(artifact_type="policy_scenario", schema_version="1"),
        ),
        produced_artifacts=(
            ArtifactContract(artifact_type="policy_scenario", schema_version="1"),
        ),
        tool_dependencies=("resolve_reform",),
    )

    async def run(self, capability_input: PolicyReformInput, context):
        referenced = await self._referenced_scenario(capability_input, context)
        resolved_year = resolve_policy_year(
            explicit_year=capability_input.year,
            referenced_year=referenced.year if referenced is not None else None,
        )
        result = await context.invoke_tool(
            "resolve_reform",
            {
                "instruction": capability_input.instruction,
                "year": resolved_year.year,
                "existing_scenario_id": (
                    referenced.artifact_id if referenced is not None else None
                ),
            },
        )
        if not isinstance(result, ResolveReformOutput):
            raise TypeError("Reform resolution returned an incompatible output.")
        if result.outcome is ReformResolutionKind.NEEDS_CLARIFICATION:
            partial = PolicyReformInput(
                instruction=capability_input.instruction,
                year=resolved_year.year,
                referenced_policy_scenario_id=(
                    referenced.artifact_id if referenced is not None else None
                ),
            )
            await context.persist_waiting(partial)
            return NeedsInput(
                prompt=result.clarification or result.summary,
                missing_fields=("instruction",),
                partial_input=partial.model_dump(mode="json", exclude_none=True),
            )
        if result.outcome is ReformResolutionKind.UNSUPPORTED:
            return Unsupported(reason=result.summary)
        if result.outcome is ReformResolutionKind.FAILED:
            return Failed(
                safe_message=result.summary,
                error_code="reform_resolution_failed",
            )
        baseline = result.outcome is ReformResolutionKind.NO_REFORM
        scenario = PolicyScenarioRef(
            provenance=ArtifactProvenance(
                conversation_id=context.conversation_id,
                turn_id=context.turn_id,
                capability_id=self.spec.identifier,
                capability_version=self.spec.version,
                invocation_id=context.capability_invocation_id,
                sources=(resolved_year.source.value, "resolve_reform"),
            ),
            year=resolved_year.year,
            scenario_revision=self._revision(resolved_year.year, result.changes),
            catalogue_version=result.catalogue_version,
            calculation_engine_version=result.calculation_engine_version,
            baseline=baseline,
            verified_changes=result.changes,
        )
        saved = await context.save_artifact(scenario)
        return Completed(
            value=PolicyReformOutput(
                scenario=saved,
                year_source=resolved_year.source,
                resolution_summary=result.summary,
            )
        )

    @staticmethod
    async def _referenced_scenario(capability_input, context):
        artifact_id = capability_input.referenced_policy_scenario_id
        if artifact_id is None:
            return None
        scenarios = await context.find_artifacts(PolicyScenarioRef)
        return next(
            (scenario for scenario in scenarios if scenario.artifact_id == artifact_id),
            None,
        )

    @staticmethod
    def _revision(year: int, changes: tuple[PolicyChange, ...]) -> str:
        payload = json.dumps(
            {
                "year": year,
                "changes": [change.model_dump(mode="json") for change in changes],
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]
