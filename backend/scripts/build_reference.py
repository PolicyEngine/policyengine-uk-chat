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


# ---------------------------------------------------------------------------
# Canonical reform recipes
# ---------------------------------------------------------------------------
# Copy-pasteable Python the agent can adapt verbatim inside `run_python`.
# Critical lesson from production: `Parameters(income_tax={"uk_brackets": [...]})`
# REPLACES the full schedule — passing a single-bracket list zeroes out the
# higher and additional rates and tanks revenue. To raise one rate you must
# pass the FULL bracket list with only that rate changed (recipe 1 below).
#
# Coverage: every field name and baseline value below was checked against
# `Parameters.model_json_schema()` and `Simulation(year=2025).get_baseline_params()`
# from policyengine-uk-compiled 0.38.0.
#
# DANGER — silent no-ops: `Parameters(...)` does NOT raise on an unknown field;
# it silently drops it, so a typo'd field name runs a BASELINE simulation and
# reports zero effect as if it were the reform's impact. (Confirmed: a wrong
# `national_insurance={"primary_threshold": ...}` dumps every NI field as None.)
# This is the same silent-wrong-result failure these recipes exist to prevent,
# so field names must match the schema EXACTLY — do not guess. The friendly
# "Unknown field … Valid: [...]" validation only fires on the dict path via
# `validate_reform` / `_build_compiled_policy`; direct `Parameters(...)` has no
# such guard. When unsure of a field name, call the `validate_reform` tool or
# consult the Parameters JSON schema section below before running.
REFORM_RECIPES = '''## Reform recipes

Canonical, copy-pasteable reforms. Each recipe constructs a *partial* override
on top of the current-law baseline — do not invent your own bracket lists from
scratch unless you really want to replace an entire schedule.

WARNING: `Parameters(...)` silently ignores unknown field names — it does NOT
raise. A misspelled field runs a baseline (no-change) simulation and reports a
zero effect as a real result. Use the exact programme keys (`income_tax`,
`national_insurance`, `child_benefit`, ...) and field names from the Parameters
JSON schema section of this reference, which is authoritative. If unsure, call
the `validate_reform` tool (it reports "Unknown field … Valid: [...]") before
running a simulation.

### 1. Basic rate +1pp (20% -> 21%) — VERIFIED

`uk_brackets` replaces the full bracket list, so always pass every band with
just the one rate changed. This is the recipe the agent was failing on in
production.

```python
reform = Parameters(income_tax={"uk_brackets": [
    {"rate": 0.21, "threshold": 0.0},        # basic (was 0.20)
    {"rate": 0.40, "threshold": 37700.0},    # higher (unchanged)
    {"rate": 0.45, "threshold": 125140.0},   # additional (unchanged)
]})
sim = Simulation(year=2025, dataset="efrs")
result = sim.run(policy=reform).budgetary_impact.model_dump()
```

### 2. Personal allowance £12,570 -> £15,000 — VERIFIED

```python
reform = Parameters(income_tax={"personal_allowance": 15000})
sim = Simulation(year=2025, dataset="efrs")
result = sim.run(policy=reform).budgetary_impact.model_dump()
```

### 3. NI primary threshold £12,570 -> £13,570 (+£1,000)

```python
# Field is `primary_threshold_annual` (annual £), baseline 12570.
reform = Parameters(national_insurance={"primary_threshold_annual": 13570})
sim = Simulation(year=2025, dataset="efrs")
result = sim.run(policy=reform).budgetary_impact.model_dump()
```

### 4. Child benefit +10% uprating

```python
# Fields are `eldest_weekly` / `additional_weekly` (weekly £).
# Baselines: eldest 26.05, additional 17.25.
reform = Parameters(child_benefit={
    "eldest_weekly": 28.66,        # 26.05 * 1.10
    "additional_weekly": 18.98,    # 17.25 * 1.10
})
sim = Simulation(year=2025, dataset="efrs")
result = sim.run(policy=reform).budgetary_impact.model_dump()
```

### 5. Marriage allowance — turn off

```python
# Field is `marriage_allowance_max_fraction` under `income_tax` (baseline 0.1);
# setting it to 0.0 removes the transferable allowance.
reform = Parameters(income_tax={"marriage_allowance_max_fraction": 0.0})
sim = Simulation(year=2025, dataset="efrs")
result = sim.run(policy=reform).budgetary_impact.model_dump()
```

### Patterns to apply across all recipes

- `Parameters(programme={...})` is a *partial* override on the named programme:
  fields you omit keep their current-law values. The one exception is list-
  valued fields like `uk_brackets`, which are replaced wholesale — always
  spell out the full list with only the rates/thresholds you intended to move.
- For distributional output, use `sim.run(policy=reform).decile_impacts` and
  `.winners_losers`; for revenue, `.budgetary_impact`.
- `dataset="efrs"` is the Enhanced FRS — usually the default for distributional
  work. Check `capabilities()` for the live list.
'''


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

    # --- Reform recipes (canonical patterns) ---
    lines.append(REFORM_RECIPES)
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
