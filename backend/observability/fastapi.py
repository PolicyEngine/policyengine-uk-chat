from __future__ import annotations

from dataclasses import replace
import os
from typing import Any

from fastapi import FastAPI
from policyengine_observability import ObservabilityConfig
from policyengine_observability import ObservabilityRuntime
from policyengine_observability import set_attribute
from policyengine_observability.adapters.fastapi import (
    init_fastapi_observability,
)

from observability.segments import SegmentName


SERVICE_NAME = "policyengine-uk-chat"
SPAN_PREFIX = "uk_chat"
UK_CHAT_METRIC_ATTRIBUTE_KEYS = (
    "gateway_outcome",
    "gateway_route",
    "gateway_tool",
    "modal_app_name",
    "modal_environment",
    "modal_function_name",
    "platform",
    "runtime_role",
)


def _environment() -> str:
    return (
        os.getenv("OBSERVABILITY_ENVIRONMENT")
        or os.getenv("DEPLOYMENT_ENVIRONMENT")
        or os.getenv("APP_ENV")
        or os.getenv("ENVIRONMENT")
        or "development"
    )


def _service_role(default: str) -> str:
    return (
        os.getenv("OBSERVABILITY_SERVICE_ROLE")
        or os.getenv("OBSERVABILITY_RUNTIME_ROLE")
        or default
    )


def _platform() -> str:
    configured = os.getenv("OBSERVABILITY_PLATFORM")
    if configured:
        return configured
    if (
        os.getenv("MODAL_ENVIRONMENT")
        or os.getenv("MODAL_TASK_ID")
        or os.getenv("OBSERVABILITY_MODAL_APP_NAME")
    ):
        return "modal"
    return "local"


def _metadata(service_role: str) -> dict[str, str]:
    values = {
        "platform": _platform(),
        "runtime_role": os.getenv("OBSERVABILITY_RUNTIME_ROLE")
        or service_role,
        "modal_environment": os.getenv("MODAL_ENVIRONMENT"),
        "modal_app_name": os.getenv("OBSERVABILITY_MODAL_APP_NAME"),
        "modal_function_name": os.getenv("OBSERVABILITY_MODAL_FUNCTION_NAME"),
    }
    return {key: value for key, value in values.items() if value}


def configure_process_observability(
    *,
    platform: str,
    service_role: str,
    runtime_role: str | None = None,
    modal_app_name: str | None = None,
    modal_function_name: str | None = None,
) -> None:
    os.environ.setdefault("OBSERVABILITY_PLATFORM", platform)
    os.environ.setdefault("OBSERVABILITY_SERVICE_ROLE", service_role)
    os.environ.setdefault(
        "OBSERVABILITY_RUNTIME_ROLE",
        runtime_role or service_role,
    )
    if modal_app_name:
        os.environ.setdefault("OBSERVABILITY_MODAL_APP_NAME", modal_app_name)
    if modal_function_name:
        os.environ.setdefault(
            "OBSERVABILITY_MODAL_FUNCTION_NAME",
            modal_function_name,
        )


class ObservabilityMetadataMiddleware:
    def __init__(self, app: Any, *, metadata: dict[str, str]) -> None:
        self.app = app
        self.metadata = metadata

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") == "http":
            try:
                for key, value in self.metadata.items():
                    set_attribute(key, value)
            except BaseException:
                pass
        await self.app(scope, receive, send)


def init_observability(
    app: FastAPI,
    *,
    service_role: str = "api",
) -> ObservabilityRuntime:
    existing = getattr(app.state, "policyengine_observability", None)
    if existing:
        return existing

    service_role = _service_role(service_role)
    config = replace(
        ObservabilityConfig.from_env(
            service_name=SERVICE_NAME,
            service_role=service_role,
            span_prefix=SPAN_PREFIX,
            instrument_fastapi=True,
            instrument_httpx=True,
            extra_metric_attribute_keys=UK_CHAT_METRIC_ATTRIBUTE_KEYS,
        ),
        environment=_environment(),
    )
    if not getattr(
        app.state,
        "policyengine_observability_metadata_middleware",
        False,
    ):
        app.add_middleware(
            ObservabilityMetadataMiddleware,
            metadata=_metadata(service_role),
        )
        app.state.policyengine_observability_metadata_middleware = True
    return init_fastapi_observability(
        app,
        config=config,
        runtime=ObservabilityRuntime(config, segment_registry=SegmentName),
        service_name=SERVICE_NAME,
        service_role=service_role,
        span_prefix=SPAN_PREFIX,
        segment_registry=SegmentName,
    )
