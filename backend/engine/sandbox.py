"""Restricted Python execution helpers used by chat tools."""

import builtins as _builtins
import json
import math
from typing import Any, Callable, Dict, List, Optional

from engine.constants import FRS_DATASET
from engine.serialization import json_safe
from engine.simulations import ensure_compiled_package_importable


ALLOWED_IMPORT_ROOTS = {
    "json",
    "math",
    "numpy",
    "pandas",
    # TEMPORARY: Python engine roots for the run_python engine override
    # (see prompts/system.py TEMPORARY_PYTHON_ENGINE_OVERRIDE). Remove
    # when reverting to the compiled engine.
    "policyengine",
    "policyengine_uk",
    "policyengine_core",
    "microdf",
}


def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    root_name = name.split(".")[0]
    if root_name not in ALLOWED_IMPORT_ROOTS:
        raise ImportError(f"Import of '{name}' is not allowed")
    return __import__(name, globals, locals, fromlist, level)


def safe_builtins(names, print_func: Optional[Callable[..., None]] = None, allow_import: bool = False):
    builtins = {name: getattr(_builtins, name) for name in names if hasattr(_builtins, name)}
    if print_func is not None:
        builtins["print"] = print_func
    if allow_import:
        builtins["__import__"] = safe_import
    return builtins


def optional_numpy():
    try:
        import numpy as np
    except ImportError:
        return None
    return np


def _safe_simulation_class(simulation_cls):
    class SafeSimulation:
        def __init__(self, *args, **kwargs):
            # The engine requires an explicit data source since 0.45. The
            # sandbox contract keeps data-less Simulation(year=...) meaning
            # the FRS survey, matching the guard bookkeeping below.
            if not any(
                kwargs.get(key) is not None
                for key in ("persons", "benunits", "households", "dataset", "data_dir")
            ):
                kwargs["dataset"] = FRS_DATASET
            object.__setattr__(
                self, "_dataset", (kwargs.get("dataset") or FRS_DATASET).lower()
            )
            object.__setattr__(
                self,
                "_is_synthetic",
                any(
                    kwargs.get(key) is not None
                    for key in ("persons", "benunits", "households")
                ),
            )
            object.__setattr__(self, "_simulation", simulation_cls(*args, **kwargs))

        @classmethod
        def single_person(cls, *args, **kwargs):
            return simulation_cls.single_person(*args, **kwargs)

        def run_microdata(self, *args, **kwargs):
            dataset = object.__getattribute__(self, "_dataset")
            is_synthetic = object.__getattribute__(self, "_is_synthetic")
            if dataset == FRS_DATASET and not is_synthetic:
                raise PermissionError(
                    "run_python cannot access FRS row-level microdata. "
                    "Use run_economy_simulation for aggregate FRS outputs."
                )
            simulation = object.__getattribute__(self, "_simulation")
            return simulation.run_microdata(*args, **kwargs)

        def __getattribute__(self, name):
            if name.startswith("_"):
                raise AttributeError(name)
            try:
                return object.__getattribute__(self, name)
            except AttributeError:
                simulation = object.__getattribute__(self, "_simulation")
                return getattr(simulation, name)

    SafeSimulation.__name__ = simulation_cls.__name__
    return SafeSimulation


class SafeCompiledModule:
    def __init__(self, module, simulation_cls):
        object.__setattr__(self, "_module", module)
        object.__setattr__(self, "_simulation_cls", simulation_cls)

    def __getattribute__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if name == "Simulation":
            return object.__getattribute__(self, "_simulation_cls")
        module = object.__getattribute__(self, "_module")
        return getattr(module, name)


def run_python_code(code: str) -> Dict[str, Any]:
    ensure_compiled_package_importable()
    import pandas as pd
    import policyengine_uk_compiled as pe
    from policyengine_uk_compiled import (
        Parameters,
        Simulation,
        StructuralReform,
        aggregate_microdata,
        capabilities,
        combine_microdata,
        ensure_dataset,
    )
    SafeSimulation = _safe_simulation_class(Simulation)
    safe_pe = SafeCompiledModule(pe, SafeSimulation)

    safe_names = (
        "range",
        "len",
        "int",
        "float",
        "str",
        "bool",
        "list",
        "dict",
        "tuple",
        "set",
        "zip",
        "enumerate",
        "map",
        "filter",
        "sorted",
        "reversed",
        "min",
        "max",
        "sum",
        "abs",
        "round",
        "True",
        "False",
        "None",
        "isinstance",
        "ValueError",
        "TypeError",
        "Exception",
        "print",
        "any",
        "all",
        "pow",
        "divmod",
        "complex",
        "type",
        "dir",
        "hasattr",
        "getattr",
    )
    output_lines: List[str] = []

    def safe_print(*args, **kwargs):
        output_lines.append(" ".join(str(arg) for arg in args))

    allowed_globals: Dict[str, Any] = {
        "__builtins__": safe_builtins(safe_names, print_func=safe_print, allow_import=True),
        "math": math,
        "json": json,
        "pd": pd,
        "pe": safe_pe,
        "Simulation": SafeSimulation,
        "StructuralReform": StructuralReform,
        "Parameters": Parameters,
        "aggregate_microdata": aggregate_microdata,
        "combine_microdata": combine_microdata,
        "capabilities": capabilities,
        "ensure_dataset": ensure_dataset,
    }
    np = optional_numpy()
    if np is not None:
        allowed_globals["np"] = np
        allowed_globals["numpy"] = np

    try:
        exec(code, allowed_globals)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}

    result = allowed_globals.get("result", None)
    response: Dict[str, Any] = {}
    if result is not None:
        response["result"] = json_safe(result)
    if output_lines:
        response["output"] = "\n".join(output_lines)
    if not response:
        response["result"] = None
        response["note"] = "No 'result' variable was set and nothing was printed."
    return response
