"""Bounded model-assisted construction of exact PolicyEngine reforms."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Callable

from config import DEFAULT_FAST_MODEL, DEFAULT_TEMPERATURE, get_sync_client
from engine.discovery import get_parameter
from engine.reforms import search_reform_targets, validate_reform_dict
from gateway.intent import ReformIntent
from tools.definitions import DEFAULT_SIMULATION_YEAR, REFORM_SCHEMA

AUTO_EXECUTE_REFORM_CONFIDENCE = 80
MAX_REFORM_SEARCHES = 4
REFORM_SEARCH_LIMIT = 20
MAX_RESOLVER_ITERATIONS = 8
MAX_ASSESSMENT_REPAIRS = 1
REFORM_RESOLVER_MODEL = os.environ.get(
    "POLICYENGINE_CHAT_REFORM_RESOLVER_MODEL",
    DEFAULT_FAST_MODEL,
)
REFORM_RESOLVER_MAX_TOKENS = int(
    os.environ.get("POLICYENGINE_CHAT_REFORM_RESOLVER_MAX_TOKENS", "2048")
)


class ReformAssessmentError(RuntimeError):
    """The resolver did not produce a structurally safe assessment."""


class GatewayCatalogueUnavailable(ReformAssessmentError):
    """The current PolicyEngine parameter catalogue could not be queried."""


@dataclass(frozen=True)
class ValidatedParameterBinding:
    parameter_path: str
    label: str
    catalogue_evidence: str


@dataclass(frozen=True)
class ReformAlternative:
    summary: str
    parameter_bindings: tuple[ValidatedParameterBinding, ...]
    reform: dict[str, Any]


@dataclass(frozen=True)
class ReformAssessment:
    reform: dict[str, Any] | None
    summary: str | None
    confidence: int
    parameter_bindings: tuple[ValidatedParameterBinding, ...]
    alternatives: tuple[ReformAlternative, ...]
    search_queries: tuple[str, ...]
    catalogue_version: str


_SEARCH_TOOL = {
    "name": "search_reform_targets",
    "description": (
        "Search current PolicyEngine UK reformable parameters. Search before "
        "constructing the reform and use only returned parameter paths."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string", "minLength": 1}},
        "required": ["query"],
        "additionalProperties": False,
    },
}

_BINDING_SCHEMA = {
    "type": "object",
    "properties": {
        "parameter_path": {"type": "string"},
        "label": {"type": "string"},
    },
    "required": ["parameter_path", "label"],
    "additionalProperties": False,
}

_ALTERNATIVE_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "reform": REFORM_SCHEMA,
        "bindings": {"type": "array", "items": _BINDING_SCHEMA},
    },
    "required": ["summary", "reform", "bindings"],
    "additionalProperties": False,
}

_ASSESSMENT_TOOL = {
    "name": "emit_reform_assessment",
    "description": (
        "Emit the best exact reform construction and calibrated confidence after "
        "searching the current parameter catalogue."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
            "reform": REFORM_SCHEMA,
            "bindings": {"type": "array", "items": _BINDING_SCHEMA},
            "alternatives": {
                "type": "array",
                "maxItems": 3,
                "items": _ALTERNATIVE_SCHEMA,
            },
        },
        "required": ["summary", "confidence", "reform", "bindings", "alternatives"],
        "additionalProperties": False,
    },
}

_SYSTEM = """You resolve a grounded UK tax-benefit reform into exact PolicyEngine
parameter changes. Search before assessing. Use only parameter paths and labels
returned by search. Search results include the current-year value and unit so
you can turn relative wording into final values. Emit one best construction,
0-100 confidence, and up to three materially plausible alternatives. Confidence
means confidence that the construction exactly represents the user's wording,
not merely that the parameter exists. Never invent a path or label."""


def current_catalogue_version() -> str:
    try:
        return version("policyengine-uk")
    except PackageNotFoundError:
        return "unknown"


def _search_with_values(query: str, limit: int) -> list[dict[str, Any]]:
    rows = search_reform_targets(query=query, limit=limit)
    enriched: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        detail = get_parameter(item["path"], DEFAULT_SIMULATION_YEAR)
        parameter = detail.get("parameter") if isinstance(detail, dict) else None
        if isinstance(parameter, dict):
            item.update(parameter)
        enriched.append(item)
    return enriched


def _validate_reform(reform: dict[str, Any], year: int) -> dict[str, Any]:
    return validate_reform_dict(reform, year=year)


def _tool_block(block: Any) -> dict[str, Any]:
    return {
        "type": "tool_use",
        "id": str(getattr(block, "id", "resolver-tool")),
        "name": str(getattr(block, "name", "")),
        "input": getattr(block, "input", {}),
    }


def _tool_result(tool_id: str, content: Any, *, is_error: bool = False) -> dict[str, Any]:
    result = {
        "type": "tool_result",
        "tool_use_id": tool_id,
        "content": json.dumps(content, default=str),
    }
    if is_error:
        result["is_error"] = True
    return result


def _binding_rows(
    raw_bindings: Any,
    reform: dict[str, Any],
    candidates: dict[str, dict[str, Any]],
) -> tuple[ValidatedParameterBinding, ...]:
    if not isinstance(raw_bindings, list):
        raise ReformAssessmentError("assessment bindings must be a list")
    bindings: list[ValidatedParameterBinding] = []
    seen: set[str] = set()
    for item in raw_bindings:
        if not isinstance(item, dict):
            raise ReformAssessmentError("assessment binding must be an object")
        path = item.get("parameter_path")
        label = item.get("label")
        if not isinstance(path, str) or path not in candidates:
            raise ReformAssessmentError(
                "assessment parameter path was not present in search results"
            )
        expected_label = candidates[path].get("label") or path
        if label != expected_label:
            raise ReformAssessmentError("assessment binding label did not match catalogue label")
        if path in seen:
            continue
        seen.add(path)
        bindings.append(
            ValidatedParameterBinding(
                parameter_path=path,
                label=label,
                catalogue_evidence=str(candidates[path].get("query", "")),
            )
        )
    if seen != set(reform):
        raise ReformAssessmentError("assessment bindings must exactly cover reform paths")
    return tuple(bindings)


def _direction_matches(
    reform: dict[str, Any],
    candidates: dict[str, dict[str, Any]],
    intent: ReformIntent,
) -> bool:
    comparable = []
    for path, proposed in reform.items():
        current = candidates[path].get("value")
        if isinstance(current, (int, float)) and isinstance(proposed, (int, float)):
            comparable.append((float(current), float(proposed)))
    if not comparable:
        return True
    if intent.action in ("increase", "uprate"):
        return all(proposed > current for current, proposed in comparable)
    if intent.action == "decrease":
        return all(proposed < current for current, proposed in comparable)
    if intent.action == "multiply" and intent.amount == "2x":
        return all(abs(proposed - current * 2) <= 1e-9 for current, proposed in comparable)
    if intent.action == "abolish":
        return all(proposed == 0 for _current, proposed in comparable)
    return True


def _validated_construction(
    raw: dict[str, Any],
    *,
    candidates: dict[str, dict[str, Any]],
    intent: ReformIntent,
    validate: Callable[[dict[str, Any], int], dict[str, Any]],
) -> tuple[dict[str, Any], tuple[ValidatedParameterBinding, ...], str]:
    reform = raw.get("reform")
    summary = raw.get("summary")
    if not isinstance(reform, dict) or not reform:
        raise ReformAssessmentError("assessment reform must be a non-empty object")
    if not isinstance(summary, str) or not summary.strip():
        raise ReformAssessmentError("assessment summary must be non-empty")
    unknown = set(reform).difference(candidates)
    if unknown:
        raise ReformAssessmentError(
            "assessment parameter path was not present in search results"
        )
    validation = validate(reform, DEFAULT_SIMULATION_YEAR)
    if not validation.get("valid"):
        raise ReformAssessmentError("assessment reform failed PolicyEngine validation")
    normalized = validation.get("normalized_reform")
    if not isinstance(normalized, dict) or set(normalized) != set(reform):
        raise ReformAssessmentError("validated reform changed the proposed paths")
    if not _direction_matches(normalized, candidates, intent):
        raise ReformAssessmentError("assessment reform contradicts the requested direction")
    bindings = _binding_rows(raw.get("bindings"), normalized, candidates)
    return normalized, bindings, summary.strip()


def _parse_assessment(
    raw: Any,
    *,
    candidates: dict[str, dict[str, Any]],
    intent: ReformIntent,
    validate: Callable[[dict[str, Any], int], dict[str, Any]],
    searches: tuple[str, ...],
    catalogue_version: str,
) -> ReformAssessment:
    if not isinstance(raw, dict):
        raise ReformAssessmentError("assessment output must be an object")
    confidence = raw.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, int) or not 0 <= confidence <= 100:
        raise ReformAssessmentError("assessment confidence must be an integer from 0 to 100")
    if raw.get("reform") == {}:
        if raw.get("bindings") != [] or raw.get("alternatives") != []:
            raise ReformAssessmentError("empty assessment cannot contain bindings or alternatives")
        summary = raw.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            raise ReformAssessmentError("assessment summary must be non-empty")
        return ReformAssessment(
            reform=None,
            summary=summary.strip(),
            confidence=confidence,
            parameter_bindings=(),
            alternatives=(),
            search_queries=searches,
            catalogue_version=catalogue_version,
        )
    reform, bindings, summary = _validated_construction(
        raw,
        candidates=candidates,
        intent=intent,
        validate=validate,
    )
    alternatives: list[ReformAlternative] = []
    raw_alternatives = raw.get("alternatives")
    if not isinstance(raw_alternatives, list):
        raise ReformAssessmentError("assessment alternatives must be a list")
    for alternative in raw_alternatives[:3]:
        if not isinstance(alternative, dict):
            raise ReformAssessmentError("assessment alternative must be an object")
        alt_reform, alt_bindings, alt_summary = _validated_construction(
            alternative,
            candidates=candidates,
            intent=intent,
            validate=validate,
        )
        alternatives.append(ReformAlternative(alt_summary, alt_bindings, alt_reform))
    return ReformAssessment(
        reform=reform,
        summary=summary,
        confidence=confidence,
        parameter_bindings=bindings,
        alternatives=tuple(alternatives),
        search_queries=searches,
        catalogue_version=catalogue_version,
    )


def assess_reform_with_catalogue(
    prompt: str,
    reform_intent: ReformIntent,
    *,
    client: Any | None = None,
    search: Callable[[str, int], list[dict[str, Any]]] = _search_with_values,
    validate: Callable[[dict[str, Any], int], dict[str, Any]] = _validate_reform,
    catalogue_version: str | None = None,
) -> ReformAssessment:
    """Search, construct, validate, and score one exact reform proposal."""

    client = client or get_sync_client()
    resolved_version = catalogue_version or current_catalogue_version()
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": (
                f"USER REQUEST:\n{prompt[:4000]}\n\n"
                f"GROUNDED REFORM INTENT:\n"
                f"policy={reform_intent.policy_phrase}\n"
                f"action={reform_intent.action}\n"
                f"amount={reform_intent.amount}\n"
                f"scope={reform_intent.scope}\n"
                f"evidence={reform_intent.evidence}"
            ),
        }
    ]
    searches: list[str] = []
    candidates: dict[str, dict[str, Any]] = {}
    repairs = 0
    last_error = "resolver did not emit an assessment"

    for _iteration in range(MAX_RESOLVER_ITERATIONS):
        response = client.messages.create(
            model=REFORM_RESOLVER_MODEL,
            max_tokens=REFORM_RESOLVER_MAX_TOKENS,
            temperature=DEFAULT_TEMPERATURE,
            system=_SYSTEM,
            tools=[_SEARCH_TOOL, _ASSESSMENT_TOOL],
            tool_choice={"type": "any"},
            messages=messages,
        )
        blocks = [block for block in response.content or [] if getattr(block, "type", None) == "tool_use"]
        if not blocks:
            last_error = "resolver returned no tool call"
            continue

        assistant_blocks = [_tool_block(block) for block in blocks]
        results: list[dict[str, Any]] = []
        emitted = None
        emitted_id = "assessment"
        for block in blocks:
            name = getattr(block, "name", None)
            tool_id = str(getattr(block, "id", "resolver-tool"))
            tool_input = getattr(block, "input", {})
            if name == "search_reform_targets":
                query = tool_input.get("query") if isinstance(tool_input, dict) else None
                query = query.strip() if isinstance(query, str) else ""
                key = query.casefold()
                if not query:
                    results.append(_tool_result(tool_id, {"error": "query is required"}, is_error=True))
                    continue
                if key in {item.casefold() for item in searches}:
                    rows = [row for row in candidates.values() if row.get("query", "").casefold() == key]
                    results.append(_tool_result(tool_id, {"query": query, "targets": rows}))
                    continue
                if len(searches) >= MAX_REFORM_SEARCHES:
                    results.append(
                        _tool_result(
                            tool_id,
                            {"error": f"search limit is {MAX_REFORM_SEARCHES}; emit the assessment now"},
                            is_error=True,
                        )
                    )
                    continue
                try:
                    rows = search(query, REFORM_SEARCH_LIMIT)
                except Exception as exc:
                    raise GatewayCatalogueUnavailable(str(exc)) from exc
                searches.append(query)
                for row in rows:
                    path = row.get("path") if isinstance(row, dict) else None
                    if not isinstance(path, str):
                        continue
                    candidate = dict(row)
                    candidate["query"] = query
                    candidates[path] = candidate
                results.append(_tool_result(tool_id, {"query": query, "targets": rows}))
            elif name == "emit_reform_assessment":
                emitted = tool_input
                emitted_id = tool_id
            else:
                results.append(_tool_result(tool_id, {"error": "unknown resolver tool"}, is_error=True))

        if emitted is not None:
            try:
                if not searches:
                    raise ReformAssessmentError("resolver must search before assessment")
                return _parse_assessment(
                    emitted,
                    candidates=candidates,
                    intent=reform_intent,
                    validate=validate,
                    searches=tuple(searches),
                    catalogue_version=resolved_version,
                )
            except ReformAssessmentError as exc:
                last_error = str(exc)
                if repairs >= MAX_ASSESSMENT_REPAIRS:
                    raise
                repairs += 1
                results.append(
                    _tool_result(
                        emitted_id,
                        {"error": last_error, "instruction": "repair and emit a valid assessment"},
                        is_error=True,
                    )
                )

        messages.append({"role": "assistant", "content": assistant_blocks})
        messages.append({"role": "user", "content": results})

    raise ReformAssessmentError(last_error)
