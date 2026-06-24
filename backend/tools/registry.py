"""Single-source registry for model-facing chat tools.

This keeps registration explicit for now: handlers still pass hand-written
schemas and descriptions to `@register_tool`. A fuller automated registration
system could derive more from typed signatures or richer tool specs, but this
small layer only removes TOOL_DEFINITIONS / TOOL_HANDLERS drift.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


ToolHandler = Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class RegisteredTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler


_TOOL_SPECS: list[RegisteredTool] = []
_TOOL_DEFINITIONS: list[dict[str, Any]] = []
_TOOL_HANDLERS: dict[str, ToolHandler] = {}


def register_tool(
    *,
    name: str,
    description: str,
    input_schema: dict[str, Any],
) -> Callable[[ToolHandler], ToolHandler]:
    """Register one model-facing tool and derive schema + dispatch views."""

    def decorator(handler: ToolHandler) -> ToolHandler:
        if name in _TOOL_HANDLERS:
            raise ValueError(f"Duplicate tool registration: {name}")

        _TOOL_SPECS.append(
            RegisteredTool(
                name=name,
                description=description,
                input_schema=input_schema,
                handler=handler,
            )
        )
        _TOOL_DEFINITIONS.append(
            {
                "name": name,
                "description": description,
                "input_schema": input_schema,
            }
        )
        _TOOL_HANDLERS[name] = handler
        return handler

    return decorator


def tool_specs() -> tuple[RegisteredTool, ...]:
    return tuple(_TOOL_SPECS)


def tool_definitions() -> list[dict[str, Any]]:
    return _TOOL_DEFINITIONS


def tool_handlers() -> dict[str, ToolHandler]:
    return _TOOL_HANDLERS
