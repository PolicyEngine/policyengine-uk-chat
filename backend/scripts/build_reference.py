"""Generate reference.md from the installed policyengine_uk_compiled library.

Output is a single markdown file loaded into the chat system prompt with
prompt caching, so the agent has up-to-date API knowledge without retraining.

Run: `docker-compose exec backend python scripts/build_reference.py`
"""

import inspect
import json
from pathlib import Path

import policyengine_uk_compiled as pe
from pydantic import BaseModel

OUT = Path(__file__).resolve().parent.parent / "reference.md"

SKIP_NAMES = {"data", "engine", "models", "structural", "download_all", "print_guide"}
PACKAGE_PREFIX = "policyengine_uk_compiled"
_BASE_MODEL_DOC = inspect.getdoc(BaseModel) or ""


def _own_doc(obj) -> str:
    """Return the object's own docstring, ignoring docs inherited from bases.

    Pydantic models inherit a ~30-line BaseModel docstring by default; without
    this filter every generated model emits that boilerplate. For classes we
    compare against each base's doc and discard matches.
    """
    doc = inspect.getdoc(obj)
    if not doc:
        return ""
    doc = doc.strip()
    if not doc:
        return ""
    if doc == _BASE_MODEL_DOC:
        return ""
    if inspect.isclass(obj):
        for base in obj.__mro__[1:]:
            if inspect.getdoc(base) == doc:
                return ""
    return doc


def _sig(obj) -> str:
    try:
        return str(inspect.signature(obj))
    except (TypeError, ValueError):
        return ""


def _from_package(obj) -> bool:
    """True if obj is defined inside policyengine_uk_compiled (not re-exported)."""
    mod = getattr(obj, "__module__", None)
    if not isinstance(mod, str):
        return False
    return mod == PACKAGE_PREFIX or mod.startswith(PACKAGE_PREFIX + ".")


def render() -> str:
    lines: list[str] = []
    lines.append("# PolicyEngine UK — Live API Reference")
    lines.append("")
    lines.append(
        "This reference is generated from the installed `policyengine_uk_compiled` "
        "library at deploy time. Treat it as the authoritative source for function "
        "signatures, parameter schemas, and engine capabilities."
    )
    lines.append("")

    # --- capabilities() snapshot ---
    lines.append("## Engine capabilities snapshot")
    lines.append("")
    lines.append("Output of `pe.capabilities()` at build time:")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(pe.capabilities(), indent=2, default=str))
    lines.append("```")
    lines.append("")

    # --- public API ---
    lines.append("## Public API")
    lines.append("")
    public = sorted(
        n for n in dir(pe)
        if not n.startswith("_") and n not in SKIP_NAMES
    )
    for name in public:
        obj = getattr(pe, name)
        kind = type(obj).__name__

        if inspect.isclass(obj) or inspect.isfunction(obj):
            # Skip re-exports from stdlib/pydantic/etc.
            if not _from_package(obj):
                continue
            sig = _sig(obj)
            doc = _own_doc(obj)
            if not sig and not doc:
                continue
            lines.append(f"### `{name}` — {kind}")
            if sig:
                lines.append("")
                lines.append(f"```python\n{name}{sig}\n```")
            if doc:
                lines.append("")
                lines.append(doc)
            lines.append("")
        else:
            # Module-level data constant: the useful signal is the value,
            # not the container class's stdlib docstring.
            value_repr = repr(obj)
            if len(value_repr) > 2000:
                value_repr = value_repr[:2000] + "... (truncated)"
            lines.append(f"### `{name}` — {kind}")
            lines.append("")
            lines.append(f"```python\n{name} = {value_repr}\n```")
            lines.append("")

    # --- Parameters schema ---
    lines.append("## Parameters JSON schema")
    lines.append("")
    lines.append(
        "Full schema of the `Parameters` pydantic model. Use this to construct "
        "reform overlays via `Parameters.model_validate({...})`."
    )
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(pe.Parameters.model_json_schema(), indent=2))
    lines.append("```")
    lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    text = render()
    OUT.write_text(text)
    size = len(text)
    print(f"wrote {OUT} — {size} chars (~{size // 4} tokens)")
