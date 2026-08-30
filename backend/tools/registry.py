"""Single-source registry for model-facing chat tools.

This keeps registration explicit for now: handlers still pass hand-written
schemas and descriptions to `@register_tool`. A fuller automated registration
system could derive more from typed signatures or richer tool specs, but this
small layer only removes TOOL_DEFINITIONS / TOOL_HANDLERS drift.

Tool registration happens as an import side effect of `tools.dispatch`. Public
read accessors lazily import that module before deriving caller-owned snapshots,
so direct `tools.registry` callers see the same registered tools as the chat
runtime without being able to mutate the canonical registry by accident.
"""

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel

from tools.contracts import CallerType, Tool, ToolSpec, Visibility


ToolHandler = Callable[..., dict[str, Any]]
ToolDefinition = dict[str, Any]


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
                input_schema=deepcopy(input_schema),
                handler=handler,
            )
        )
        return handler

    return decorator


def _ensure_registered() -> None:
    # Importing dispatch runs the decorators that populate this registry.
    import tools.dispatch  # noqa: F401


def tool_specs() -> tuple[RegisteredTool, ...]:
    """Return caller-owned snapshots of registered tool specs in model order."""

    _ensure_registered()
    return tuple(
        RegisteredTool(
            name=spec.name,
            description=spec.description,
            input_schema=deepcopy(spec.input_schema),
            handler=spec.handler,
        )
        for spec in _TOOL_SPECS
    )


def tool_definitions() -> list[ToolDefinition]:
    """Return fresh JSON-like tool-definition snapshots in model order.

    Callers may mutate the returned list/dicts for a local model/eval request,
    but mutation is not a registration mechanism and does not affect future
    registry reads. Register or remove model-facing tools only through
    `@register_tool`.
    """

    _ensure_registered()
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "input_schema": deepcopy(spec.input_schema),
        }
        for spec in _TOOL_SPECS
    ]


def tool_handlers() -> Mapping[str, ToolHandler]:
    """Return a read-only handler mapping in model order."""

    _ensure_registered()
    return MappingProxyType({spec.name: spec.handler for spec in _TOOL_SPECS})


class ToolRegistry:
    """Validated registry for the capability runtime's typed tools."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool[Any, Any]] = {}

    def register(self, tool: Tool[Any, Any]) -> None:
        spec = getattr(tool, "spec", None)
        if not isinstance(spec, ToolSpec):
            raise TypeError("Typed tools must declare a valid ToolSpec.")
        if spec.identifier in self._tools:
            raise ValueError(f"Duplicate typed tool registration: {spec.identifier}")
        if not issubclass(spec.input_model, BaseModel) or not issubclass(
            spec.output_model, BaseModel
        ):
            raise TypeError(
                f"Tool {spec.identifier} input and output declarations must be Pydantic models."
            )
        self._tools[spec.identifier] = tool

    def get(self, identifier: str, *, caller: CallerType) -> Tool[Any, Any]:
        try:
            tool = self._tools[identifier]
        except KeyError as exc:
            raise KeyError(f"Unknown typed tool: {identifier}") from exc
        if caller not in tool.spec.allowed_callers:
            raise PermissionError(
                f"Caller {caller.value} may not invoke tool {identifier}."
            )
        return tool

    def definitions_for(
        self,
        caller: CallerType,
        *,
        include_private: bool = False,
    ) -> list[dict[str, Any]]:
        definitions = []
        for tool in self._tools.values():
            spec = tool.spec
            if caller not in spec.allowed_callers:
                continue
            if spec.visibility is Visibility.PRIVATE and not include_private:
                continue
            definitions.append(
                {
                    "name": spec.identifier,
                    "description": spec.description,
                    "input_schema": deepcopy(spec.input_model.model_json_schema()),
                }
            )
        return definitions

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(tool.spec for tool in self._tools.values())

    def registered(self, identifier: str) -> Tool[Any, Any]:
        try:
            return self._tools[identifier]
        except KeyError as exc:
            raise KeyError(f"Unknown typed tool: {identifier}") from exc

    def validate(self) -> None:
        for identifier, tool in self._tools.items():
            if identifier != tool.spec.identifier:
                raise ValueError(f"Typed tool registry key drift for {identifier}.")
