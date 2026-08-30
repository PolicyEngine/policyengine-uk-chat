"""Explicit startup composition for typed tools and capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable
from typing import Any

from capabilities.contracts import Capability
from capabilities.executor import InvocationExecutor
from capabilities.registry import CapabilityRegistry
from capabilities.tracing import InvocationTracer
from tools.contracts import Tool
from tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class RuntimeComposition:
    tools: ToolRegistry
    capabilities: CapabilityRegistry
    tracer: InvocationTracer
    executor: InvocationExecutor


def compose_runtime(
    *,
    tools: Iterable[Tool[Any, Any]],
    capabilities: Iterable[Capability[Any, Any]],
    tracer: InvocationTracer | None = None,
) -> RuntimeComposition:
    tool_registry = ToolRegistry()
    for tool in tools:
        tool_registry.register(tool)
    tool_registry.validate()

    capability_registry = CapabilityRegistry()
    for capability in capabilities:
        capability_registry.register(capability)
    capability_registry.validate()

    registered_tool_ids = {spec.identifier for spec in tool_registry.specs()}
    for tool_spec in tool_registry.specs():
        missing = set(tool_spec.tool_dependencies) - registered_tool_ids
        if missing:
            raise ValueError(
                f"Tool {tool_spec.identifier} requires unknown tools: {sorted(missing)}."
            )
    for capability_spec in capability_registry.specs():
        missing = set(capability_spec.tool_dependencies) - registered_tool_ids
        if missing:
            raise ValueError(
                f"Capability {capability_spec.identifier} requires unknown tools: {sorted(missing)}."
            )

    invocation_tracer = tracer or InvocationTracer()
    executor = InvocationExecutor(
        tools=tool_registry,
        capabilities=capability_registry,
        tracer=invocation_tracer,
    )
    return RuntimeComposition(
        tools=tool_registry,
        capabilities=capability_registry,
        tracer=invocation_tracer,
        executor=executor,
    )
