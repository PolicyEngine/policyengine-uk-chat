"""Single-source registry for model-facing chat tools.

This keeps registration explicit for now: handlers still pass hand-written
schemas and descriptions to `@register_tool`. A fuller automated registration
system could derive more from typed signatures or richer tool specs, but this
small layer only removes TOOL_DEFINITIONS / TOOL_HANDLERS drift.

Tool registration happens as an import side effect of `tools.dispatch`. Public
read accessors lazily import that module before deriving immutable registry
views, so direct `tools.registry` callers see the same registered tools as the
chat runtime.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


ToolHandler = Callable[..., dict[str, Any]]
ToolDefinition = Mapping[str, Any]


@dataclass(frozen=True)
class RegisteredTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler


_TOOL_SPECS: list[RegisteredTool] = []


def register_tool(
    *,
    name: str,
    description: str,
    input_schema: dict[str, Any],
) -> Callable[[ToolHandler], ToolHandler]:
    """Register one model-facing tool and derive schema + dispatch views."""

    def decorator(handler: ToolHandler) -> ToolHandler:
        if any(spec.name == name for spec in _TOOL_SPECS):
            raise ValueError(f"Duplicate tool registration: {name}")

        _TOOL_SPECS.append(
            RegisteredTool(
                name=name,
                description=description,
                input_schema=input_schema,
                handler=handler,
            )
        )
        return handler

    return decorator


def _ensure_registered() -> None:
    # Importing dispatch runs the decorators that populate this registry.
    import tools.dispatch  # noqa: F401


def tool_specs() -> tuple[RegisteredTool, ...]:
    _ensure_registered()
    return tuple(_TOOL_SPECS)


def tool_definitions() -> tuple[ToolDefinition, ...]:
    _ensure_registered()
    return tuple(
        MappingProxyType({
            "name": spec.name,
            "description": spec.description,
            "input_schema": spec.input_schema,
        })
        for spec in _TOOL_SPECS
    )


def tool_handlers() -> Mapping[str, ToolHandler]:
    _ensure_registered()
    return MappingProxyType({spec.name: spec.handler for spec in _TOOL_SPECS})
