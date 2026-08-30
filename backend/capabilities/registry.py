"""Registry and startup validation for typed capabilities."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from capabilities.contracts import Capability, CapabilitySpec
from tools.contracts import CallerType, Visibility


class CapabilityRegistry:
    def __init__(self) -> None:
        self._capabilities: dict[str, Capability[Any, Any]] = {}

    def register(self, capability: Capability[Any, Any]) -> None:
        spec = getattr(capability, "spec", None)
        if not isinstance(spec, CapabilitySpec):
            raise TypeError("Capabilities must declare a valid CapabilitySpec.")
        if spec.identifier in self._capabilities:
            raise ValueError(f"Duplicate capability registration: {spec.identifier}")
        if not issubclass(spec.input_model, BaseModel) or not issubclass(
            spec.output_model, BaseModel
        ):
            raise TypeError(
                f"Capability {spec.identifier} input and output declarations must be Pydantic models."
            )
        self._capabilities[spec.identifier] = capability

    def get(
        self,
        identifier: str,
        *,
        caller: CallerType,
    ) -> Capability[Any, Any]:
        try:
            capability = self._capabilities[identifier]
        except KeyError as exc:
            raise KeyError(f"Unknown capability: {identifier}") from exc
        if caller not in capability.spec.allowed_callers:
            raise PermissionError(
                f"Caller {caller.value} may not invoke capability {identifier}."
            )
        return capability

    def descriptions_for(
        self,
        caller: CallerType,
        *,
        include_private: bool = False,
    ) -> tuple[dict[str, object], ...]:
        descriptions: list[dict[str, object]] = []
        for capability in self._capabilities.values():
            spec = capability.spec
            if caller not in spec.allowed_callers:
                continue
            if spec.visibility is Visibility.PRIVATE and not include_private:
                continue
            descriptions.append(
                {
                    "identifier": spec.identifier,
                    "version": spec.version,
                    "description": spec.description,
                    "required_use": spec.required_use,
                    "input_schema": spec.input_model.model_json_schema(),
                }
            )
        return tuple(descriptions)

    def specs(self) -> tuple[CapabilitySpec, ...]:
        return tuple(capability.spec for capability in self._capabilities.values())

    def registered(self, identifier: str) -> Capability[Any, Any]:
        try:
            return self._capabilities[identifier]
        except KeyError as exc:
            raise KeyError(f"Unknown capability: {identifier}") from exc

    def validate(self) -> None:
        self._validate_dependencies()
        self._validate_cycles()

    def _validate_dependencies(self) -> None:
        for consumer in self._capabilities.values():
            for dependency in consumer.spec.dependencies:
                provider = self._capabilities.get(dependency.capability_id)
                if provider is None:
                    raise ValueError(
                        f"Capability {consumer.spec.identifier} requires unknown capability "
                        f"{dependency.capability_id}."
                    )
                if dependency.artifact is None:
                    continue
                if not any(
                    dependency.artifact.is_compatible_with(produced)
                    for produced in provider.spec.produced_artifacts
                ):
                    raise ValueError(
                        f"Capability {consumer.spec.identifier} requires incompatible "
                        f"artifact {dependency.artifact.artifact_type} "
                        f"from {dependency.capability_id}."
                    )
                if not any(
                    accepted.is_compatible_with(dependency.artifact)
                    for accepted in consumer.spec.accepted_artifacts
                ):
                    raise ValueError(
                        f"Capability {consumer.spec.identifier} does not declare accepted "
                        f"artifact {dependency.artifact.artifact_type}."
                    )

    def _validate_cycles(self) -> None:
        visiting: list[str] = []
        visited: set[str] = set()

        def visit(identifier: str) -> None:
            if identifier in visited:
                return
            if identifier in visiting:
                start = visiting.index(identifier)
                cycle = visiting[start:] + [identifier]
                raise ValueError(
                    "Capability dependency cycle: " + " -> ".join(cycle)
                )
            visiting.append(identifier)
            capability = self._capabilities[identifier]
            for dependency in capability.spec.dependencies:
                if dependency.capability_id in self._capabilities:
                    visit(dependency.capability_id)
            visiting.pop()
            visited.add(identifier)

        for identifier in self._capabilities:
            visit(identifier)
