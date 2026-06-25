"""Microdata loading, filtering, and aggregate operations."""

import hashlib
import json
from typing import Any, Dict, List, Optional

from engine.reforms import build_compiled_policy
from engine.serialization import json_safe
from engine.simulations import DATASET_LABELS, build_simulation


_microdata_cache: Dict[tuple, Any] = {}
_MAX_CACHE = 4

# These are manually enumerated because policyengine-uk-compiled does not yet
# expose programmatic output-column metadata or recommended defaults for plain
# versus comparison microdata modes. Replace this with package metadata when
# PolicyEngine/policyengine-uk-v2#107 is available.
_PLAIN_DEFAULT_COLUMNS = {
    "persons": [
        "age",
        "gender",
        "employment_income",
        "self_employment_income",
        "income_tax",
        "employee_ni",
        "total_income",
    ],
    "benunits": [
        "total_benefits",
        "universal_credit",
        "child_benefit",
    ],
    "households": [
        "region",
        "net_income",
        "total_tax",
        "total_benefits",
    ],
}

_COMPARISON_DEFAULT_COLUMNS = {
    "persons": [
        "age",
        "gender",
        "employment_income",
        "self_employment_income",
        "baseline_income_tax",
        "reform_income_tax",
        "income_tax_change",
        "baseline_total_income",
        "reform_total_income",
        "total_income_change",
    ],
    "benunits": [
        "baseline_total_benefits",
        "reform_total_benefits",
        "total_benefits_change",
        "baseline_universal_credit",
        "reform_universal_credit",
        "baseline_child_benefit",
        "reform_child_benefit",
    ],
    "households": [
        "region",
        "baseline_net_income",
        "reform_net_income",
        "net_income_change",
        "baseline_total_tax",
        "reform_total_tax",
        "baseline_total_benefits",
        "reform_total_benefits",
    ],
}


def hash_reform(reform: Optional[Dict[str, Any]]) -> str:
    if reform is None:
        return "none"
    return hashlib.md5(json.dumps(reform, sort_keys=True).encode()).hexdigest()


def get_cached_microdata(year: int, reform: Optional[Dict[str, Any]], dataset: str):
    """Return cached MicrodataResult."""
    key = (year, hash_reform(reform), dataset)
    if key not in _microdata_cache:
        policy = build_compiled_policy(reform)
        sim = build_simulation(year, dataset)
        _microdata_cache[key] = sim.run_microdata(policy=policy)
        if len(_microdata_cache) > _MAX_CACHE:
            del _microdata_cache[next(iter(_microdata_cache))]
    return _microdata_cache[key]


def _default_value_columns(entity: str, columns: List[str], reform_applied: bool) -> List[str]:
    defaults = _COMPARISON_DEFAULT_COLUMNS if reform_applied else _PLAIN_DEFAULT_COLUMNS
    return [column for column in defaults.get(entity, []) if column in columns]


def analyse_microdata_result(
    microdata,
    entity: str,
    operation: str,
    year: int,
    dataset_key: str,
    reform_applied: bool,
    filters: Optional[Dict[str, Any]] = None,
    columns: Optional[List[str]] = None,
    group_by: Optional[List[str]] = None,
    n: int = 5,
) -> Dict[str, Any]:
    import pandas as pd

    entity_map = {"persons": microdata.persons, "benunits": microdata.benunits, "households": microdata.households}
    if entity not in entity_map:
        return {"error": "entity must be one of: persons, benunits, households"}
    df = entity_map[entity].copy()

    weights = microdata.households[["household_id", "weight"]].copy()
    if "household_id" in df.columns and "weight" not in df.columns:
        df = df.merge(weights, on="household_id", how="left")
    elif "weight" not in df.columns:
        df["weight"] = 1.0

    change_pairs = {
        "persons": [
            ("income_tax", "baseline_income_tax", "reform_income_tax"),
            ("employee_ni", "baseline_employee_ni", "reform_employee_ni"),
            ("total_income", "baseline_total_income", "reform_total_income"),
        ],
        "benunits": [
            ("total_benefits", "baseline_total_benefits", "reform_total_benefits"),
            ("universal_credit", "baseline_universal_credit", "reform_universal_credit"),
            ("child_benefit", "baseline_child_benefit", "reform_child_benefit"),
        ],
        "households": [
            ("net_income", "baseline_net_income", "reform_net_income"),
            ("total_tax", "baseline_total_tax", "reform_total_tax"),
            ("total_benefits", "baseline_total_benefits", "reform_total_benefits"),
        ],
    }
    for change_col, base_col, reform_col in change_pairs.get(entity, []):
        if base_col in df.columns and reform_col in df.columns:
            df[f"{change_col}_change"] = df[reform_col] - df[base_col]

    filters_applied = {}
    if filters:
        for col, fval in filters.items():
            if col not in df.columns:
                return {"error": f"Column '{col}' not found. Available: {list(df.columns)}"}
            filters_applied[col] = fval
            if isinstance(fval, dict):
                if "min" in fval:
                    df = df[df[col] >= fval["min"]]
                if "max" in fval:
                    df = df[df[col] <= fval["max"]]
                if "gt" in fval:
                    df = df[df[col] > fval["gt"]]
                if "lt" in fval:
                    df = df[df[col] < fval["lt"]]
                if "gte" in fval:
                    df = df[df[col] >= fval["gte"]]
                if "lte" in fval:
                    df = df[df[col] <= fval["lte"]]
                if "ne" in fval:
                    df = df[df[col] != fval["ne"]]
            elif isinstance(fval, list):
                df = df[df[col].isin(fval)]
            else:
                df = df[df[col] == fval]

    row_count = len(df)
    weighted_count = int(df["weight"].sum()) if "weight" in df.columns else row_count
    all_cols = list(df.columns)

    if columns:
        missing = [c for c in columns if c not in df.columns]
        if missing:
            return {"error": f"Columns not found: {missing}. Available: {all_cols}"}
        value_cols = columns
    else:
        value_cols = _default_value_columns(entity, all_cols, reform_applied)

    if operation == "sample":
        actual_n = min(n, 20, row_count)
        sample_df = df[value_cols].sample(n=actual_n, random_state=42) if row_count >= actual_n else df[value_cols]
        result = [
            {k: (None if (isinstance(v, float) and str(v) == "nan") else v) for k, v in row.items()}
            for row in sample_df.to_dict(orient="records")
        ]
    elif operation == "mean":
        numeric_cols = [c for c in value_cols if pd.api.types.is_numeric_dtype(df[c]) and c != "weight"]
        result = {
            c: float((df[c] * df["weight"]).sum() / df["weight"].sum())
            if df["weight"].sum() > 0
            else float(df[c].mean())
            for c in numeric_cols
        }
    elif operation == "sum":
        numeric_cols = [c for c in value_cols if pd.api.types.is_numeric_dtype(df[c]) and c != "weight"]
        result = {c: float((df[c] * df["weight"]).sum()) for c in numeric_cols}
    elif operation == "count":
        result = {"row_count": row_count, "weighted_population": weighted_count}
    elif operation == "group_by":
        if not group_by:
            return {"error": "group_by operation requires at least one group_by column"}
        missing_groups = [c for c in group_by if c not in df.columns]
        if missing_groups:
            return {"error": f"Group columns not found: {missing_groups}. Available: {all_cols}"}
        numeric_cols = [c for c in value_cols if pd.api.types.is_numeric_dtype(df[c]) and c != "weight"]
        grouped_rows = []
        for keys, group in df.groupby(group_by, dropna=False):
            if not isinstance(keys, tuple):
                keys = (keys,)
            row = {col: json_safe(value) for col, value in zip(group_by, keys)}
            row["row_count"] = int(len(group))
            row["weighted_population"] = float(group["weight"].sum())
            for col in numeric_cols:
                row[col] = (
                    float((group[col] * group["weight"]).sum() / group["weight"].sum())
                    if group["weight"].sum() > 0
                    else float(group[col].mean())
                )
            grouped_rows.append(row)
        result = grouped_rows
    elif operation == "describe":
        numeric_cols = [c for c in value_cols if pd.api.types.is_numeric_dtype(df[c]) and c != "weight"]
        result = {
            c: {
                "mean": float((df[c] * df["weight"]).sum() / df["weight"].sum())
                if df["weight"].sum() > 0
                else float(df[c].mean()),
                "min": float(df[c].min()),
                "max": float(df[c].max()),
                "count": int(df[c].count()),
            }
            for c in numeric_cols
        }
        for col in [c for c in value_cols if not pd.api.types.is_numeric_dtype(df[c])]:
            result[col] = {str(k): int(v) for k, v in df[col].value_counts().head(10).items()}
    else:
        return {"error": f"Unknown operation '{operation}'. Use: mean, sum, count, sample, group_by, describe"}

    return {
        "entity": entity,
        "operation": operation,
        "year": year,
        "dataset": DATASET_LABELS.get(dataset_key, dataset_key),
        "reform_applied": reform_applied,
        "filters_applied": filters_applied,
        "row_count": row_count,
        "weighted_count": weighted_count,
        "result": result,
        "available_columns": all_cols,
    }
