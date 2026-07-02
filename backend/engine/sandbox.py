"""Restricted Python execution helpers used by chat tools."""

import builtins as _builtins
import json
import math
from typing import Any, Callable, Dict, List, Optional

from engine.serialization import json_safe
from engine.simulations import ensure_compiled_package_importable


ALLOWED_IMPORT_ROOTS = {"json", "math", "numpy", "pandas"}


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
            object.__setattr__(self, "_dataset", (kwargs.get("dataset") or "frs").lower())
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
        def _wrap(cls, simulation, dataset=None, is_synthetic=True):
            """Wrap an already-built raw simulation without re-running it.

            Factory classmethods use this so a raw ``simulation_cls`` instance is
            never handed back to sandboxed code, where ``type(...)`` could
            otherwise recover the unguarded class and call
            ``RawSimulation(dataset="frs").run_microdata()`` to defeat the FRS
            row-level guard.
            """
            wrapped = object.__new__(cls)
            object.__setattr__(wrapped, "_dataset", dataset)
            object.__setattr__(wrapped, "_is_synthetic", is_synthetic)
            object.__setattr__(wrapped, "_simulation", simulation)
            return wrapped

        @classmethod
        def single_person(cls, *args, **kwargs):
            result = simulation_cls.single_person(*args, **kwargs)
            # The pinned compiled contract returns (persons, benunits,
            # households) frames, which pass through untouched. Guard against a
            # contract that hands back a raw Simulation instance by wrapping it
            # so the raw class stays hidden. single_person is synthetic by
            # construction, so the FRS row-level guard stays satisfied.
            if isinstance(result, simulation_cls):
                return cls._wrap(result)
            return result

        def run_microdata(self, *args, **kwargs):
            dataset = object.__getattribute__(self, "_dataset")
            is_synthetic = object.__getattribute__(self, "_is_synthetic")
            if dataset == "frs" and not is_synthetic:
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


def run_generator(code: str) -> Dict[str, Any]:
    """Execute a Python generator snippet that returns a dict of tool kwargs."""
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
        "append",
    )
    allowed_globals: Dict[str, Any] = {
        "__builtins__": safe_builtins(safe_names),
        "math": math,
        "json": json,
    }
    exec(code, allowed_globals)
    if "generate" not in allowed_globals:
        raise ValueError("Generator code must define a `generate()` function")
    result = allowed_globals["generate"]()
    if not isinstance(result, dict):
        raise ValueError(f"generate() must return a dict, got {type(result).__name__}")
    return result
