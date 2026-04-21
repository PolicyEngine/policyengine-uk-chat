"""Generate reference.md from the installed policyengine_uk_compiled library.

Output is a single markdown file loaded into the chat system prompt with
prompt caching, so the agent has up-to-date API knowledge without retraining.

Run: `docker-compose exec backend python scripts/build_reference.py`
"""

import inspect
import json
from pathlib import Path

import policyengine_uk_compiled as pe

OUT = Path(__file__).resolve().parent.parent / "reference.md"

SKIP_NAMES = {"data", "engine", "models", "structural", "download_all", "print_guide"}


def _doc(obj) -> str:
    return (inspect.getdoc(obj) or "").strip()


def _sig(obj) -> str:
    try:
        return str(inspect.signature(obj))
    except (TypeError, ValueError):
        return ""


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
        doc = _doc(obj)
        if not doc:
            continue
        sig = _sig(obj)
        kind = type(obj).__name__
        lines.append(f"### `{name}` — {kind}")
        if sig:
            lines.append("")
            lines.append(f"```python\n{name}{sig}\n```")
        lines.append("")
        lines.append(doc)
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
