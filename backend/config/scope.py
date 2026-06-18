"""Loading the engine-derived scope descriptor."""

from pathlib import Path

# scope_descriptor.md is generated next to reference.md at the backend/ root by
# engine/reference.py. config/ is one level under backend/, so resolve up two.
_SCOPE_DESCRIPTOR_PATH = Path(__file__).resolve().parent.parent / "scope_descriptor.md"


def load_scope_descriptor(default: str) -> str:
    """Read the engine-generated `scope_descriptor.md`, falling back to `default`
    (the curated DEFAULT_SCOPE_DESCRIPTOR) when it hasn't been built locally."""
    try:
        return _SCOPE_DESCRIPTOR_PATH.read_text().strip()
    except FileNotFoundError:
        return default
