"""Restricted Python execution helpers used by chat tools."""

import builtins as _builtins
import json
import math
from typing import Any, Callable, Dict, List, Optional

from tooling.serialization import json_safe
from tooling.simulations import ensure_compiled_package_importable


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


def compile_structural_hook(code: str):
    """Compile a structural hook from code defining hook(...)."""
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
        "print",
        "any",
        "all",
        "pow",
        "divmod",
    )
    try:
        import pandas as pd
    except ImportError as exc:
        raise ImportError("pandas is required for structural reform hooks") from exc

    allowed_globals: Dict[str, Any] = {
        "__builtins__": safe_builtins(safe_names),
        "math": math,
        "json": json,
        "pd": pd,
    }
    np = optional_numpy()
    if np is not None:
        allowed_globals["np"] = np
        allowed_globals["numpy"] = np

    exec(code, allowed_globals)
    hook = allowed_globals.get("hook")
    if hook is None or not callable(hook):
        raise ValueError("Structural hook code must define a callable `hook(year, persons, benunits, households)`")
    return hook


def build_structural_reform(structural_reform: Optional[Dict[str, Any]]):
    if not structural_reform:
        return None
    if not isinstance(structural_reform, dict):
        raise ValueError(f"structural_reform must be a dict, got {type(structural_reform).__name__}")

    unknown = set(structural_reform) - {"pre", "post"}
    if unknown:
        raise ValueError(f"Unknown structural_reform field(s): {sorted(unknown)}. Valid: ['pre', 'post']")

    ensure_compiled_package_importable()
    from policyengine_uk_compiled import StructuralReform

    pre = structural_reform.get("pre")
    post = structural_reform.get("post")
    if pre is not None and not isinstance(pre, str):
        raise ValueError("structural_reform.pre must be a string of Python code defining hook(...)")
    if post is not None and not isinstance(post, str):
        raise ValueError("structural_reform.post must be a string of Python code defining hook(...)")

    return StructuralReform(
        pre=compile_structural_hook(pre) if pre else None,
        post=compile_structural_hook(post) if post else None,
    )


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
        "pe": pe,
        "Simulation": Simulation,
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

