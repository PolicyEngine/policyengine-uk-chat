"""Feature configuration for the optional billing subsystem."""

import os


def billing_enabled() -> bool:
    """Return whether credit enforcement and usage accounting are enabled.

    Billing is deliberately opt-in. Only the literal value ``true`` enables it.
    """

    return os.environ.get("BILLING_ENABLED", "false") == "true"
