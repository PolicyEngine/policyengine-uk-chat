from __future__ import annotations

from dataclasses import replace
import inspect
import logging
import os

from fastapi import FastAPI
from policyengine_observability import ObservabilityConfig
from policyengine_observability import ObservabilityRuntime
from policyengine_observability.adapters.fastapi import (
    init_fastapi_observability,
)

from observability.segments import SegmentName


logger = logging.getLogger(__name__)

SERVICE_NAME = "policyengine-uk-chat"
SPAN_PREFIX = "uk_chat"
DEPLOYED_PLATFORMS = frozenset(("google_cloud_run", "modal"))
UK_CHAT_METRIC_ATTRIBUTE_KEYS = (
    "cloud_run_configuration",
    "cloud_run_revision",
    "cloud_run_service",
    "google_cloud_project",
    "modal_app_name",
    "modal_environment",
    "modal_function_name",
    "platform",
    "runtime_role",
)
_PROCESS_METADATA: dict[str, str] = {}


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
        _PROCESS_METADATA.get("service_role")
        or _PROCESS_METADATA.get("runtime_role")
        or default
    )


def _platform() -> str:
    if platform := _PROCESS_METADATA.get("platform"):
        return platform
    if platform := os.getenv("OBSERVABILITY_PLATFORM"):
        return platform
    if os.getenv("K_SERVICE") or os.getenv("K_REVISION"):
        return "google_cloud_run"
    if (
        os.getenv("MODAL_ENVIRONMENT")
        or os.getenv("MODAL_TASK_ID")
        or _PROCESS_METADATA.get("modal_app_name")
    ):
        return "modal"
    return "local"


def _default_log_destinations(platform: str) -> tuple[str, ...]:
    if platform in DEPLOYED_PLATFORMS:
        return ("google_cloud_logging",)
    return ("stdout",)


def _metadata(service_role: str, platform: str) -> dict[str, str]:
    values = {
        "platform": platform,
        "runtime_role": _PROCESS_METADATA.get("runtime_role") or service_role,
        "cloud_run_service": os.getenv("K_SERVICE"),
        "cloud_run_revision": os.getenv("K_REVISION"),
        "cloud_run_configuration": os.getenv("K_CONFIGURATION"),
        "google_cloud_project": os.getenv("OBSERVABILITY_GOOGLE_CLOUD_PROJECT")
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("GCP_PROJECT")
        or os.getenv("GCLOUD_PROJECT"),
        "modal_environment": os.getenv("MODAL_ENVIRONMENT"),
        "modal_app_name": _PROCESS_METADATA.get("modal_app_name"),
        "modal_function_name": _PROCESS_METADATA.get("modal_function_name"),
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
    _PROCESS_METADATA["platform"] = platform
    _PROCESS_METADATA["service_role"] = service_role
    _PROCESS_METADATA["runtime_role"] = runtime_role or service_role
    if modal_app_name:
        _PROCESS_METADATA["modal_app_name"] = modal_app_name
    else:
        _PROCESS_METADATA.pop("modal_app_name", None)
    if modal_function_name:
        _PROCESS_METADATA["modal_function_name"] = modal_function_name
    else:
        _PROCESS_METADATA.pop("modal_function_name", None)


def init_observability(
    app: FastAPI,
    *,
    service_role: str = "api",
) -> ObservabilityRuntime:
    existing = getattr(app.state, "policyengine_observability", None)
    if existing:
        return existing

    service_role = _service_role(service_role)
    platform = _platform()
    supports_default_log_destinations = (
        "default_log_destinations"
        in inspect.signature(ObservabilityConfig.from_env).parameters
    )
    config_kwargs = {
        "service_name": SERVICE_NAME,
        "service_role": service_role,
        "span_prefix": SPAN_PREFIX,
        "instrument_fastapi": supports_default_log_destinations,
        "instrument_httpx": True,
        "extra_metric_attribute_keys": UK_CHAT_METRIC_ATTRIBUTE_KEYS,
    }
    if supports_default_log_destinations:
        config_kwargs["default_log_destinations"] = _default_log_destinations(platform)
    config = replace(
        ObservabilityConfig.from_env(**config_kwargs),
        environment=_environment(),
    )
    # One startup line so a misrouted destination or missing workload
    # identity token is visible in the first seconds of any container.
    logger.info(
        "Observability initialized: platform=%s log_destinations=%s "
        "modal_identity_token=%s",
        platform,
        ",".join(getattr(config, "log_destinations", ())),
        "present" if os.getenv("MODAL_IDENTITY_TOKEN") else "absent",
    )
    return init_fastapi_observability(
        app,
        config=config,
        runtime=ObservabilityRuntime(config, segment_registry=SegmentName),
        service_name=SERVICE_NAME,
        service_role=service_role,
        span_prefix=SPAN_PREFIX,
        segment_registry=SegmentName,
        static_attributes=_metadata(service_role, platform),
    )
