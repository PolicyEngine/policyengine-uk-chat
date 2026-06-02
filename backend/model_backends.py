"""
Model backend adapters for the chat agent.

The chat UI currently exposes one flexible Python execution tool. These
adapters keep that contract stable while allowing the preloaded model
interface, prompt context, and tool documentation to vary by backend.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
import json
import math
from pathlib import Path
import sys
from typing import Any, Callable, Dict, Iterable


class BackendImportError(RuntimeError):
    """Raised when a selected model backend cannot be imported."""


def _ensure_sibling_package_importable(
    import_name: str,
    sibling_candidates: Iterable[Path],
) -> None:
    try:
        __import__(import_name)
        return
    except ModuleNotFoundError:
        pass

    added_paths = []
    for candidate in sibling_candidates:
        if candidate.is_dir():
            candidate_str = str(candidate)
            if candidate_str not in sys.path:
                sys.path.insert(0, candidate_str)
                added_paths.append(candidate_str)

    try:
        __import__(import_name)
        return
    except ModuleNotFoundError as exc:
        detail = str(exc)

    raise BackendImportError(
        f"{import_name} is not importable. Install the package or make sure a "
        "local checkout is available next to policyengine-uk-chat. "
        f"Added paths: {added_paths or 'none'}. Import error: {detail}"
    )


@dataclass(frozen=True)
class ModelBackend:
    id: str
    display_name: str
    package_name: str
    package_label: str
    import_roots: frozenset[str]

    def package_version(self) -> str:
        try:
            return version(self.package_name)
        except PackageNotFoundError:
            return "unknown"

    def prompt_context(self) -> str:
        raise NotImplementedError

    def tool_description(self) -> str:
        raise NotImplementedError

    def execution_globals(self) -> Dict[str, Any]:
        raise NotImplementedError


class UKCompiledBackend(ModelBackend):
    def __init__(self) -> None:
        super().__init__(
            id="uk_compiled",
            display_name="PolicyEngine UK compiled Rust backend",
            package_name="policyengine-uk-compiled",
            package_label="policyengine-uk-compiled",
            import_roots=frozenset(
                {"json", "math", "numpy", "pandas", "policyengine_uk_compiled"}
            ),
        )

    def _ensure_importable(self) -> None:
        repo_parent = Path(__file__).resolve().parents[2]
        _ensure_sibling_package_importable(
            "policyengine_uk_compiled",
            [
                repo_parent / "policyengine-uk-rust" / "interfaces" / "python",
                repo_parent
                / "policyengine-uk-rust-codex-debug-issue"
                / "interfaces"
                / "python",
            ],
        )

    def prompt_context(self) -> str:
        return """CRITICAL - USE THE OFFICIAL POLICYENGINE UK COMPILED INTERFACE:
- The selected backend is `uk_compiled`, the Rust-backed UK engine exposed through `policyengine_uk_compiled`.
- The Python environment preloads:
  `policyengine_uk_compiled` as `pe`
  `Simulation`
  `Parameters`
  `StructuralReform`
  `aggregate_microdata`
  `combine_microdata`
  `capabilities`
  `ensure_dataset`
  `pd`, `np`, `json`, `math`
- Prefer writing code directly against those objects so the run is reproducible outside chat.
- Do not recreate policy logic manually if the package already provides it.

COMMON WORKFLOWS FOR THIS BACKEND:
- Baseline economy-wide run:
  `caps = capabilities()`
  `sim = Simulation(year=2025, dataset="frs")`
  `result = sim.run().model_dump()`
- Reform run:
  `policy = Parameters.model_validate({"income_tax": {"personal_allowance": 15000}})`
  `result = sim.run(policy=policy).model_dump()`
- Custom household run:
  build `persons`, `benunits`, and `households` DataFrames, then pass them to `Simulation(...)`
- Microdata analysis:
  `micro = sim.run_microdata(...)` then analyse `micro.persons`, `micro.benunits`, or `micro.households` with pandas

MODELLING SCOPE:
- The compiled backend covers the model surface exposed by `policyengine_uk_compiled`.
- Use `capabilities()` to check what is available locally before committing to an approach."""

    def tool_description(self) -> str:
        return (
            "Execute reproducible Python code using the PolicyEngine UK compiled "
            "backend. The environment preloads `policyengine_uk_compiled` as `pe`, "
            "plus `Simulation`, `Parameters`, `StructuralReform`, "
            "`aggregate_microdata`, `combine_microdata`, `capabilities`, "
            "`ensure_dataset`, `pd`, `np`, `json`, and `math`. Assign the final "
            "answer to `result` and use `print()` for short diagnostics."
        )

    def execution_globals(self) -> Dict[str, Any]:
        self._ensure_importable()
        import pandas as pd
        import policyengine_uk_compiled as pe

        try:
            import numpy as np
        except ImportError:
            np = None

        from policyengine_uk_compiled import (
            Parameters,
            Simulation,
            StructuralReform,
            aggregate_microdata,
            capabilities,
            combine_microdata,
            ensure_dataset,
        )

        globals_dict: Dict[str, Any] = {
            "math": math,
            "json": json,
            "pd": pd,
            "pe": pe,
            "Simulation": Simulation,
            "StructuralReform": StructuralReform,
            "Parameters": Parameters,
            "aggregate_microdata": aggregate_microdata,
            "combine_microdata": combine_microdata,
            "capabilities": capabilities,
            "ensure_dataset": ensure_dataset,
        }
        if np is not None:
            globals_dict["np"] = np
            globals_dict["numpy"] = np
        return globals_dict


class UKPolicyEnginePythonBackend(ModelBackend):
    def __init__(self) -> None:
        super().__init__(
            id="uk_python",
            display_name="PolicyEngine UK Python backend",
            package_name="policyengine-uk",
            package_label="policyengine-uk",
            import_roots=frozenset(
                {
                    "json",
                    "math",
                    "numpy",
                    "pandas",
                    "policyengine",
                    "policyengine_core",
                    "policyengine_uk",
                    "microdf",
                }
            ),
        )

    def _ensure_importable(self) -> None:
        repo_parent = Path(__file__).resolve().parents[2]
        _ensure_sibling_package_importable(
            "policyengine_uk",
            [
                repo_parent / "policyengine-core",
                repo_parent / "policyengine.py" / "src",
                repo_parent / "policyengine-uk",
            ],
        )

    def prompt_context(self) -> str:
        return """TOOL ROUTING - READ THIS FIRST:

Society-wide / economy-wide / population-level reform question?
  → CALL `run_economy_simulation` ON YOUR FIRST TURN. Do not call
    `run_python` first to "explore the API" or "check the interface".
    Do not call `capabilities()`. Go straight to `run_economy_simulation`.

Reform expressible in the programme/field shape (see list below)?
  → `run_economy_simulation` is REQUIRED. Falling back to `run_python`
    for an expressible reform is a bug: it produces drifting numbers
    across runs and wastes 10-30 tool calls relearning the engine.

Reform NOT in the mapped field list, OR question isn't about a reform?
  → Then and only then, use `run_python`.

`run_economy_simulation` shape:
- args: `{year, reform, dataset}`. `year` defaults to 2025; `dataset`
  defaults to "efrs" (Enhanced FRS, the same data PE-API uses).
- `reform` is a programme→field→value dict, NOT dotted parameter paths.
- Returns a structured dict: `budget.{budgetary_impact, tax_revenue_impact,
  benefit_spending_impact}`, `decile.{average, relative}` keyed 1-10,
  `poverty.poverty.{all, child, adult, senior}.{baseline, reform}`.
- Numbers match PE-API's `/uk/economy` endpoint by construction (same
  engine, same methodology).

Worked examples (copy this shape):
- Raise personal allowance to £15,000:
  `{"reform": {"income_tax": {"personal_allowance": 15000}}}`
- Basic rate 20% to 21%:
  `{"reform": {"income_tax": {"basic_rate": 0.21}}}`
- NI main rate 8% to 6%:
  `{"reform": {"national_insurance": {"main_rate": 0.06}}}`
- Stacked: basic+higher rates + NI cut:
  `{"reform": {"income_tax": {"basic_rate": 0.22, "higher_rate": 0.42},
               "national_insurance": {"main_rate": 0.06}}}`

Mapped programme/field combinations (ONLY these are expressible):
- income_tax: personal_allowance, basic_rate, higher_rate, additional_rate
- national_insurance: main_rate, primary_threshold
- child_benefit: eldest_amount, additional_amount

If your reform uses any other field, the tool will return an error
listing valid fields. THEN fall back to `run_python`.

ABOUT THIS BACKEND:
- Selected backend is `uk_python`, the Python `policyengine-uk` model package.
- This is the PolicyEngine Core / OpenFisca-style UK model, not the compiled
  Rust wrapper.

PYTHON ENVIRONMENT (for `run_python` fallback only):
- Preloaded: `policyengine_uk` as `pe`, `Simulation`, `Microsimulation`,
  `CountryTaxBenefitSystem`, `Scenario`, `capabilities`, `pd`, `np`,
  `json`, `math`. The higher-level `policyengine` package is preloaded
  when installed.
- Custom household: `sim = Simulation(situation={...})`,
  `result = sim.calculate("household_net_income", 2025).tolist()`.
- Microsimulation from published UK data:
  `sim = Microsimulation(dataset="hf://policyengine/policyengine-uk-data/enhanced_frs_2023_24.h5")`
- Parameter reform NOT expressible via `run_economy_simulation`:
  pass a dotted-path `reform=` dict to `Microsimulation(...)`:
  `reform = {"gov.hmrc.income_tax.allowances.personal_allowance.amount": {"2025-01-01.2025-12-31": 15000}}`
  `sim = Microsimulation(dataset=..., reform=reform)`

MODELLING SCOPE:
- This backend exposes the Python `policyengine-uk` model surface. Its API, datasets, variables, and results can differ from `uk_compiled`.
- If a dataset is unavailable locally or requires a download/token, report that clearly instead of guessing."""

    def tool_description(self) -> str:
        return (
            "Execute reproducible Python code using the Python `policyengine-uk` "
            "backend. The environment preloads `policyengine_uk` as `pe`, "
            "`Simulation`, `Microsimulation`, `CountryTaxBenefitSystem`, "
            "`Scenario`, `capabilities`, `pd`, `np`, `json`, and `math`; "
            "the higher-level `policyengine` package is available when installed. "
            "Assign the final answer to `result` and use `print()` for short diagnostics."
        )

    def execution_globals(self) -> Dict[str, Any]:
        self._ensure_importable()
        import pandas as pd
        import policyengine_uk as pe

        try:
            import numpy as np
        except ImportError:
            np = None

        try:
            import policyengine
        except ImportError:
            policyengine = None

        from policyengine_uk import (
            CountryTaxBenefitSystem,
            Microsimulation,
            Simulation,
        )
        from policyengine_uk.utils.scenario import Scenario

        def capabilities() -> Dict[str, Any]:
            system = CountryTaxBenefitSystem()
            variables = system.variables
            parameters = system.parameters
            return {
                "backend": self.id,
                "display_name": self.display_name,
                "package": "policyengine-uk",
                "interface": "Python PolicyEngine Core/OpenFisca-style model",
                "preloaded": [
                    "policyengine_uk as pe",
                    "Simulation",
                    "Microsimulation",
                    "CountryTaxBenefitSystem",
                    "Scenario",
                    "pd",
                    "np",
                    "json",
                    "math",
                ],
                "variable_count": len(variables),
                "sample_variables": sorted(variables)[:50],
                "parameter_root_children": sorted(parameters.children.keys()),
                "dataset_notes": [
                    "Pass a situation dict for household-style calculations.",
                    "Pass a UKSingleYearDataset, UKMultiYearDataset, DataFrame, or hf:// URL for microsimulation.",
                    "No default dataset is used unless POLICYENGINE_UK_DEFAULT_DATASET is set.",
                ],
                "comparison_note": (
                    "Results may differ from uk_compiled because this backend uses "
                    "the Python policyengine-uk model and its datasets/API surface."
                ),
            }

        globals_dict: Dict[str, Any] = {
            "math": math,
            "json": json,
            "pd": pd,
            "pe": pe,
            "policyengine_uk": pe,
            "Simulation": Simulation,
            "Microsimulation": Microsimulation,
            "CountryTaxBenefitSystem": CountryTaxBenefitSystem,
            "Scenario": Scenario,
            "capabilities": capabilities,
        }
        if policyengine is not None:
            globals_dict["policyengine"] = policyengine
        if np is not None:
            globals_dict["np"] = np
            globals_dict["numpy"] = np
        return globals_dict


_BACKENDS: Dict[str, ModelBackend] = {
    "uk_compiled": UKCompiledBackend(),
    "uk_python": UKPolicyEnginePythonBackend(),
}


def available_backends() -> Dict[str, Dict[str, str]]:
    return {
        backend_id: {
            "id": backend.id,
            "display_name": backend.display_name,
            "package_label": backend.package_label,
            "version": backend.package_version(),
        }
        for backend_id, backend in _BACKENDS.items()
    }


def get_backend(backend_id: str | None = None) -> ModelBackend:
    selected = backend_id or "uk_compiled"
    if selected not in _BACKENDS:
        valid = ", ".join(sorted(_BACKENDS))
        raise ValueError(f"Unknown model backend '{selected}'. Valid backends: {valid}")
    return _BACKENDS[selected]


def make_backend_importer(backend: ModelBackend) -> Callable[..., Any]:
    def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
        root_name = name.split(".")[0]
        if root_name not in backend.import_roots:
            raise ImportError(
                f"Import of '{name}' is not allowed for backend '{backend.id}'"
            )
        return __import__(name, globals, locals, fromlist, level)

    return _safe_import
