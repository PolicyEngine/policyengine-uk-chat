"""The gateway: a cheap pre-pass that routes the opening turn.

runtime.py builds the grounded plan and runs the forced-tool classifier;
policy.py is the pure, offline-testable gate (criticality + outcome). This
package re-exports the runtime surface so callers do `from gateway import X`.
"""

from gateway.runtime import (
    GatewayVerdict,
    gateway_writer_directive,
    run_gateway,
    serialise_plan_for_system,
)

__all__ = [
    "GatewayVerdict",
    "run_gateway",
    "gateway_writer_directive",
    "serialise_plan_for_system",
]
