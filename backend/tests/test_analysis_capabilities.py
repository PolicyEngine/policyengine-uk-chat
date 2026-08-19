from __future__ import annotations

from dataclasses import replace

import pytest

from analysis.capabilities import CAPABILITY_REGISTRY
from analysis.common import AnalysisError, AnalysisErrorCode
from tools.registry import tool_specs


def _operation_specs():
    return {spec.name: spec for spec in tool_specs()}


def _assert_invalid(registry, message: str):
    with pytest.raises(AnalysisError) as raised:
        registry.validate(_operation_specs())
    assert raised.value.code == AnalysisErrorCode.CAPABILITY_INVALID
    assert message in str(raised.value)


def test_registry_matches_every_registered_field_output_and_operation():
    CAPABILITY_REGISTRY.validate(_operation_specs())
    for kind, capability in CAPABILITY_REGISTRY.capabilities.items():
        assert all(
            CAPABILITY_REGISTRY.field_for(field, kind)
            for field in capability.semantic_fields
        )
        assert all(
            CAPABILITY_REGISTRY.producer_for(kind, output)
            for output in capability.supported_outputs
        )


def test_registry_rejects_unknown_semantic_field():
    capability = CAPABILITY_REGISTRY.capabilities["society"]
    registry = replace(
        CAPABILITY_REGISTRY,
        capabilities={
            **CAPABILITY_REGISTRY.capabilities,
            "society": replace(
                capability,
                semantic_fields=capability.semantic_fields | {"invented_field"},
            ),
        },
    )
    _assert_invalid(registry, "unknown fields")


def test_registry_rejects_output_without_producer():
    registry = replace(
        CAPABILITY_REGISTRY,
        producers={
            key: value
            for key, value in CAPABILITY_REGISTRY.producers.items()
            if key != "poverty_impact"
        },
    )
    _assert_invalid(registry, "has no output producer")


def test_registry_rejects_missing_operation():
    producer = CAPABILITY_REGISTRY.producers["poverty_impact"]
    registry = replace(
        CAPABILITY_REGISTRY,
        producers={
            **CAPABILITY_REGISTRY.producers,
            "poverty_impact": replace(producer, operation="missing_operation"),
        },
    )
    _assert_invalid(registry, "names missing operation")


def test_registry_rejects_incompatible_operation_arguments():
    producer = CAPABILITY_REGISTRY.producers["budgetary_impact"]
    registry = replace(
        CAPABILITY_REGISTRY,
        producers={
            **CAPABILITY_REGISTRY.producers,
            "budgetary_impact": replace(
                producer,
                operation_arguments=producer.operation_arguments
                | {"model_invented_argument"},
            ),
        },
    )
    _assert_invalid(registry, "supplies unknown arguments")


def test_registry_rejects_result_type_drift():
    producer = CAPABILITY_REGISTRY.producers["budgetary_impact"]
    registry = replace(
        CAPABILITY_REGISTRY,
        producers={
            **CAPABILITY_REGISTRY.producers,
            "budgetary_impact": replace(producer, result_type="wrong_type"),
        },
    )
    _assert_invalid(registry, "returns budgetary_impact")


def test_registry_rejects_duplicate_required_default():
    capability = CAPABILITY_REGISTRY.capabilities["society"]
    registry = replace(
        CAPABILITY_REGISTRY,
        capabilities={
            **CAPABILITY_REGISTRY.capabilities,
            "society": replace(
                capability,
                defaults={**capability.defaults, "analysis_kind": "society"},
            ),
        },
    )
    _assert_invalid(registry, "both requires and defaults")
