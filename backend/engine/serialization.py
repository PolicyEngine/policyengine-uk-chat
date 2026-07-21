"""Serialization helpers for tool outputs."""

import math
from typing import Any, Dict, List


def json_safe(obj: Any) -> Any:
    try:
        import numpy as np
    except ImportError:
        np = None

    try:
        import pandas as pd
    except ImportError:
        pd = None

    if obj is None or isinstance(obj, (str, int, bool)):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if np is not None:
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            value = float(obj)
            return value if math.isfinite(value) else None
        if isinstance(obj, np.bool_):
            return bool(obj)
    if pd is not None:
        if isinstance(obj, (pd.DataFrame, pd.Series)):
            raise TypeError(
                "Tool outputs must serialize typed result fields, not tabular "
                "simulation data."
            )
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [json_safe(v) for v in obj]
    if hasattr(obj, "model_dump") and callable(obj.model_dump):
        return json_safe(obj.model_dump())
    if hasattr(obj, "dict") and callable(obj.dict):
        return json_safe(obj.dict())
    try:
        import dataclasses

        if dataclasses.is_dataclass(obj):
            return json_safe(dataclasses.asdict(obj))
    except Exception:
        pass
    return str(obj)


def explore_tabular_data(data: List[Dict[str, Any]], max_unique_values: int = 20) -> Dict[str, Any]:
    if not data or not isinstance(data[0], dict):
        return {"error": "Data must be a non-empty list of dicts", "row_count": 0, "columns": []}
    row_count = len(data)
    all_keys = set()
    for row in data:
        all_keys.update(row.keys())
    columns = []
    for key in sorted(all_keys):
        values = [row.get(key) for row in data]
        sample_type = next((type(v).__name__ for v in values if v is not None), "unknown")
        unique_values = list(set(v for v in values if v is not None))
        unique_count = len(unique_values)
        col_info = {
            "name": key,
            "type": sample_type,
            "unique_count": unique_count,
            "null_count": sum(1 for v in values if v is None),
        }
        if unique_count <= max_unique_values:
            try:
                col_info["unique_values"] = sorted(unique_values)
            except TypeError:
                col_info["unique_values"] = unique_values
        if sample_type in ("int", "float"):
            numeric = [v for v in values if isinstance(v, (int, float))]
            if numeric:
                col_info["min"] = min(numeric)
                col_info["max"] = max(numeric)
        columns.append(col_info)
    return {"row_count": row_count, "columns": columns}
