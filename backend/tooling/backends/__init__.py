"""Engine-backend strategies for the typed-tool layer.

The typed tools (``run_economy_simulation``, ``calculate_household``,
``analyse_microdata``) are engine-agnostic at the call-site. This package
holds the per-engine implementations and a single dispatcher.

The ``backend_id`` matches ``backend/model_backends.py``'s identifiers
(``uk_python``, ``uk_compiled``) so the same selector the chat already
uses to pick a ``run_python`` execution environment also picks the typed
tool implementation.
"""

from typing import Protocol, Any, Dict, Optional


class EngineBackend(Protocol):
    """A typed-tool execution surface backed by one PolicyEngine engine."""

    backend_id: str

    def run_economy_simulation(
        self,
        year: int,
        reform: Optional[Dict[str, Any]],
        dataset: str,
    ) -> Dict[str, Any]:
        ...


_BACKENDS: Dict[str, EngineBackend] = {}


def register(backend: EngineBackend) -> None:
    _BACKENDS[backend.backend_id] = backend


def get_backend(backend_id: str) -> EngineBackend:
    if backend_id not in _BACKENDS:
        raise ValueError(
            f"Unknown typed-tool backend: {backend_id!r}. "
            f"Registered: {sorted(_BACKENDS)}"
        )
    return _BACKENDS[backend_id]


# Side-effect imports register the available engines on package load.
from tooling.backends import uk_python  # noqa: E402,F401
