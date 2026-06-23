"""Deterministic metadata lookup helpers for model-facing tools."""

from engine.lookup.parameters import lookup_parameter_metadata
from engine.lookup.variables import lookup_variable_metadata

__all__ = ["lookup_parameter_metadata", "lookup_variable_metadata"]
