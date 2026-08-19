from __future__ import annotations

from enum import StrEnum
from typing import Any

from policyengine_observability import UNKNOWN_SEGMENT
from policyengine_observability import coerce_segment_name as _coerce_segment


class SegmentName(StrEnum):
    UNKNOWN = UNKNOWN_SEGMENT

    ANALYSIS_INTERPRET = "analysis.interpret"
    ANALYSIS_BIND = "analysis.bind"
    ANALYSIS_COMPILE = "analysis.compile"
    ANALYSIS_EXECUTE = "analysis.execute"
    ANALYSIS_NARRATE = "analysis.narrate"
    MODEL_SELECT = "model.select"
    SYSTEM_BUILD = "system.build"
    TOOL_SCHEMA_BUILD = "tool_schema.build"
    MODEL_ITERATION = "model.iteration"
    MODEL_STREAM = "model.stream"
    TOOL_EXECUTE = "tool.execute"
    BILLING_CHECK_BALANCE = "billing.check_balance"
    BILLING_RECORD_USAGE = "billing.record_usage"
    SUGGESTIONS = "suggestions"
    TITLE_GENERATE = "title.generate"


def coerce_segment_name(value: Any) -> tuple[str, bool]:
    return _coerce_segment(value, registry=SegmentName)
