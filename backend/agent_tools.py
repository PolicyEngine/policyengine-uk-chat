"""
Agent tools for the microsim chatbot.
Wraps compiled PolicyEngine UK simulations and utility operations.
"""

import hashlib
import json
import logging
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _ensure_compiled_package_importable() -> None:
    """Make the local policyengine_uk_compiled package importable in dev setups."""
    try:
        import policyengine_uk_compiled  # noqa: F401
        return
    except ModuleNotFoundError:
        pass

    repo_parent = Path(__file__).resolve().parents[2]
    candidates = [
        repo_parent / "policyengine-uk-rust" / "interfaces" / "python",
        repo_parent / "policyengine-uk-rust-codex-debug-issue" / "interfaces" / "python",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            candidate_str = str(candidate)
            if candidate_str not in sys.path:
                sys.path.insert(0, candidate_str)
            try:
                import policyengine_uk_compiled  # noqa: F401
                return
            except ModuleNotFoundError:
                continue

    raise ModuleNotFoundError(
        "policyengine_uk_compiled is not importable. Install the package or make sure a local "
        "policyengine-uk-rust checkout with interfaces/python is available."
    )

# ---------------------------------------------------------------------------
# Microdata cache
# ---------------------------------------------------------------------------
_microdata_cache: Dict[tuple, Any] = {}
_MAX_CACHE = 4


def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    allowed_roots = {"json", "math", "numpy", "pandas"}
    root_name = name.split(".")[0]
    if root_name not in allowed_roots:
        raise ImportError(f"Import of '{name}' is not allowed")
    return __import__(name, globals, locals, fromlist, level)


def _json_safe(obj: Any) -> Any:
    try:
        import numpy as np
    except ImportError:
        np = None

    try:
        import pandas as pd
    except ImportError:
        pd = None

    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if np is not None:
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.bool_):
            return bool(obj)
    if pd is not None:
        if isinstance(obj, pd.DataFrame):
            return obj.to_dict(orient="records")
        if isinstance(obj, pd.Series):
            return obj.to_list()
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_json_safe(v) for v in obj]
    if hasattr(obj, "model_dump") and callable(obj.model_dump):
        return _json_safe(obj.model_dump())
    if hasattr(obj, "dict") and callable(obj.dict):
        return _json_safe(obj.dict())
    try:
        import dataclasses
        if dataclasses.is_dataclass(obj):
            return _json_safe(dataclasses.asdict(obj))
    except Exception:
        pass
    return str(obj)


def _hash_reform(reform: Optional[Dict[str, Any]]) -> str:
    if not reform:
        return "none"
    return hashlib.md5(json.dumps(reform, sort_keys=True).encode()).hexdigest()


def _get_cached_microdata(year: int, reform: Optional[Dict[str, Any]], dataset: str, structural=None):
    """Return cached MicrodataResult. Structural reforms always run fresh."""
    if structural is not None:
        policy = _build_compiled_policy(reform)
        sim = _build_simulation(year, dataset)
        return sim.run_microdata(policy=policy, structural=structural)
    key = (year, _hash_reform(reform), dataset)
    if key not in _microdata_cache:
        policy = _build_compiled_policy(reform)
        sim = _build_simulation(year, dataset)
        _microdata_cache[key] = sim.run_microdata(policy=policy)
        if len(_microdata_cache) > _MAX_CACHE:
            del _microdata_cache[next(iter(_microdata_cache))]
    return _microdata_cache[key]


def get_capabilities() -> Dict[str, Any]:
    try:
        _ensure_compiled_package_importable()
        from policyengine_uk_compiled import capabilities
        return capabilities()
    except Exception as e:
        logger.error(f"Error getting capabilities: {e}")
        return {"error": str(e)}


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
        col_info = {"name": key, "type": sample_type, "unique_count": unique_count, "null_count": sum(1 for v in values if v is None)}
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


def _build_compiled_policy(reform: Optional[Dict[str, Any]]):
    if not reform:
        return None
    _ensure_compiled_package_importable()
    from policyengine_uk_compiled import (
        Parameters, IncomeTaxParams, NationalInsuranceParams, UniversalCreditParams,
        ChildBenefitParams, StatePensionParams, PensionCreditParams, BenefitCapParams,
        HousingBenefitParams, TaxCreditsParams, ScottishChildPaymentParams,
        StampDutyParams, StampDutyBand, CapitalGainsTaxParams, WealthTaxParams,
    )
    param_cls_map = {
        "income_tax": IncomeTaxParams,
        "national_insurance": NationalInsuranceParams,
        "universal_credit": UniversalCreditParams,
        "child_benefit": ChildBenefitParams,
        "state_pension": StatePensionParams,
        "pension_credit": PensionCreditParams,
        "benefit_cap": BenefitCapParams,
        "housing_benefit": HousingBenefitParams,
        "tax_credits": TaxCreditsParams,
        "scottish_child_payment": ScottishChildPaymentParams,
        "stamp_duty": StampDutyParams,
        "capital_gains_tax": CapitalGainsTaxParams,
        "wealth_tax": WealthTaxParams,
    }
    kwargs = {}
    for program, fields in reform.items():
        if program not in param_cls_map:
            raise ValueError(f"Unknown reform program '{program}'. Valid: {list(param_cls_map)}")
        if not isinstance(fields, dict):
            raise ValueError(f"Reform program '{program}' must be a dict, got {type(fields).__name__}")
        cls = param_cls_map[program]
        # stamp_duty bands is a list of dicts — convert to StampDutyBand objects
        if cls is StampDutyParams and "bands" in fields and fields["bands"] is not None:
            fields = {**fields, "bands": [StampDutyBand(**b) if isinstance(b, dict) else b for b in fields["bands"]]}
        valid_fields = set(cls.model_fields)
        unknown = {k for k in fields if k not in valid_fields and fields[k] is not None}
        if unknown:
            raise ValueError(f"Unknown field(s) for '{program}': {sorted(unknown)}. Valid: {sorted(valid_fields)}")
        kwargs[program] = cls(**{k: v for k, v in fields.items() if v is not None})
    return Parameters(**kwargs) if kwargs else None


def get_baseline_parameters(year: int = 2025) -> Dict[str, Any]:
    try:
        _ensure_compiled_package_importable()
        from policyengine_uk_compiled import Simulation
        sim = Simulation(year=year)
        return {"year": year, "parameters": sim.get_baseline_params()}
    except Exception as e:
        logger.error(f"Error getting baseline parameters: {e}")
        return {"error": str(e)}


def calculate_household(
    person: List[Dict[str, Any]],
    benunit: List[Dict[str, Any]],
    household: List[Dict[str, Any]],
    year: int = 2025,
    reform: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    try:
        import pandas as pd
        from policyengine_uk_compiled import Simulation, PERSON_DEFAULTS, BENUNIT_DEFAULTS, HOUSEHOLD_DEFAULTS

        def fill_defaults(records, defaults):
            return pd.DataFrame([{**defaults, **rec} for rec in records])

        # Remap IDs to 0-based (the compiled engine uses IDs as array indices)
        hh_id_map = {rec["household_id"]: i for i, rec in enumerate(household)}
        bu_id_map = {rec["benunit_id"]: i for i, rec in enumerate(benunit)}
        person = [
            {**rec, "person_id": i, "benunit_id": bu_id_map[rec["benunit_id"]], "household_id": hh_id_map[rec["household_id"]]}
            for i, rec in enumerate(person)
        ]
        benunit = [
            {**rec, "benunit_id": bu_id_map[rec["benunit_id"]], "household_id": hh_id_map[rec["household_id"]]}
            for rec in benunit
        ]
        household = [
            {**rec, "household_id": hh_id_map[rec["household_id"]]}
            for rec in household
        ]

        # Set is_benunit_head/is_household_head: first adult (age>=16) per unit is head
        seen_bu_heads = set()
        seen_hh_heads = set()
        for rec in person:
            bu_id = rec["benunit_id"]
            hh_id = rec["household_id"]
            is_adult = rec.get("age", 30) >= 16
            rec["is_benunit_head"] = is_adult and bu_id not in seen_bu_heads
            rec["is_household_head"] = is_adult and hh_id not in seen_hh_heads
            if rec["is_benunit_head"]:
                seen_bu_heads.add(bu_id)
            if rec["is_household_head"]:
                seen_hh_heads.add(hh_id)

        persons_df = fill_defaults(person, PERSON_DEFAULTS)
        benunits_df = fill_defaults(benunit, BENUNIT_DEFAULTS)
        households_df = fill_defaults(household, HOUSEHOLD_DEFAULTS)

        if "person_ids" not in benunits_df.columns or (benunits_df["person_ids"] == BENUNIT_DEFAULTS.get("person_ids", 0)).all():
            # Build comma-separated person_ids for each benunit from persons_df
            bu_to_persons = persons_df.groupby("benunit_id")["person_id"].apply(lambda ids: ",".join(str(i) for i in ids))
            benunits_df["person_ids"] = benunits_df["benunit_id"].map(bu_to_persons).fillna(benunits_df["benunit_id"].astype(str))
        if "benunit_ids" not in households_df.columns or (households_df["benunit_ids"] == HOUSEHOLD_DEFAULTS.get("benunit_ids", 0)).all():
            hh_to_benunits = benunits_df.groupby("household_id")["benunit_id"].apply(lambda ids: ",".join(str(i) for i in ids))
            households_df["benunit_ids"] = households_df["household_id"].map(hh_to_benunits).fillna(households_df["household_id"].astype(str))
        if "person_ids" not in households_df.columns or (households_df["person_ids"] == HOUSEHOLD_DEFAULTS.get("person_ids", 0)).all():
            hh_to_persons = persons_df.groupby("household_id")["person_id"].apply(lambda ids: ",".join(str(i) for i in ids))
            households_df["person_ids"] = households_df["household_id"].map(hh_to_persons).fillna(households_df["household_id"].astype(str))

        sim = Simulation(year=year, persons=persons_df, benunits=benunits_df, households=households_df)
        policy = _build_compiled_policy(reform)
        result = sim.run_microdata(policy=policy)

        def df_to_records(df):
            return [{k: (None if (hasattr(v, '__class__') and v.__class__.__name__ == 'float' and str(v) == 'nan') else v) for k, v in row.items()} for row in df.to_dict(orient="records")]

        return {
            "status": "success",
            "year": year,
            "reform_applied": reform is not None,
            "person": df_to_records(result.persons),
            "benunit": df_to_records(result.benunits),
            "household": df_to_records(result.households),
        }
    except Exception as e:
        logger.error(f"Error in calculate_household: {e}")
        import traceback; logger.error(traceback.format_exc())
        return {"error": str(e)}
def _build_simulation(year: int, dataset: str = "frs"):
    """Build a Simulation with the right data source and CLI flags."""
    _ensure_compiled_package_importable()
    from policyengine_uk_compiled import Simulation
    return Simulation(year=year, dataset=dataset)


def _compile_structural_hook(code: str):
    """Compile a structural hook from code defining `hook(...)`.

    The hook signature must be:
        hook(year, persons, benunits, households) -> (persons, benunits, households)
    """
    import math
    import builtins as _builtins

    safe_names = (
        "range", "len", "int", "float", "str", "bool", "list", "dict",
        "tuple", "set", "zip", "enumerate", "map", "filter", "sorted",
        "reversed", "min", "max", "sum", "abs", "round", "True", "False",
        "None", "isinstance", "ValueError", "TypeError", "print",
        "any", "all", "pow", "divmod",
    )
    safe_builtins = {k: getattr(_builtins, k) for k in safe_names if hasattr(_builtins, k)}

    try:
        import numpy as np
    except ImportError:
        np = None

    try:
        import pandas as pd
    except ImportError as e:
        raise ImportError("pandas is required for structural reform hooks") from e

    allowed_globals: Dict[str, Any] = {
        "__builtins__": safe_builtins,
        "math": math,
        "json": json,
        "pd": pd,
    }
    if np is not None:
        allowed_globals["np"] = np
        allowed_globals["numpy"] = np

    exec(code, allowed_globals)
    hook = allowed_globals.get("hook")
    if hook is None or not callable(hook):
        raise ValueError("Structural hook code must define a callable `hook(year, persons, benunits, households)`")
    return hook


def _build_structural_reform(structural_reform: Optional[Dict[str, Any]]):
    if not structural_reform:
        return None
    if not isinstance(structural_reform, dict):
        raise ValueError(f"structural_reform must be a dict, got {type(structural_reform).__name__}")

    unknown = set(structural_reform) - {"pre", "post"}
    if unknown:
        raise ValueError(f"Unknown structural_reform field(s): {sorted(unknown)}. Valid: ['pre', 'post']")

    _ensure_compiled_package_importable()
    from policyengine_uk_compiled import StructuralReform

    pre = structural_reform.get("pre")
    post = structural_reform.get("post")
    if pre is not None and not isinstance(pre, str):
        raise ValueError("structural_reform.pre must be a string of Python code defining hook(...)")
    if post is not None and not isinstance(post, str):
        raise ValueError("structural_reform.post must be a string of Python code defining hook(...)")

    return StructuralReform(
        pre=_compile_structural_hook(pre) if pre else None,
        post=_compile_structural_hook(post) if post else None,
    )


def run_economy_simulation(
    year: int = 2025,
    reform: Optional[Dict[str, Any]] = None,
    dataset: str = "frs",
    structural_reform: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    try:
        policy = _build_compiled_policy(reform)
        structural = _build_structural_reform(structural_reform)
        sim = _build_simulation(year, dataset)
        # Always run baseline to compute program-level changes
        baseline_result = sim.run()
        if structural is not None:
            from policyengine_uk_compiled import aggregate_microdata, combine_microdata
            baseline_microdata = sim.run_microdata()
            reform_microdata = sim.run_microdata(policy=policy, structural=structural)
            combined_microdata = combine_microdata(baseline_microdata, reform_microdata)
            reform_result = aggregate_microdata(
                combined_microdata.persons,
                combined_microdata.benunits,
                combined_microdata.households,
                year,
            )
        else:
            reform_result = sim.run(policy=policy) if policy else baseline_result

        baseline_breakdown = baseline_result.program_breakdown.model_dump()
        reform_breakdown = reform_result.program_breakdown.model_dump()
        program_changes = {
            k: {"baseline": baseline_breakdown[k], "reform": reform_breakdown[k], "change": reform_breakdown[k] - baseline_breakdown[k]}
            for k in baseline_breakdown
        }

        dataset_labels = {"frs": "Family Resources Survey", "efrs": "Enhanced FRS", "spi": "Survey of Personal Incomes", "lcfs": "Living Costs and Food Survey", "was": "Wealth and Assets Survey"}
        return {
            "fiscal_year": reform_result.fiscal_year,
            "dataset": dataset_labels.get(dataset, dataset),
            "budgetary_impact": reform_result.budgetary_impact.model_dump(),
            "program_breakdown_changes": program_changes,
            "decile_impacts": [d.model_dump() for d in reform_result.decile_impacts],
            "winners_losers": reform_result.winners_losers.model_dump(),
            "caseloads": reform_result.caseloads.model_dump(),
            "baseline_hbai_incomes": baseline_result.baseline_hbai_incomes.model_dump(),
            "reform_hbai_incomes": reform_result.reform_hbai_incomes.model_dump(),
            "baseline_poverty": baseline_result.baseline_poverty.model_dump(),
            "reform_poverty": reform_result.reform_poverty.model_dump(),
            "structural_reform_applied": structural is not None,
        }
    except FileNotFoundError as e:
        return {"error": f"{dataset.upper()} microdata not available", "detail": str(e), "hint": "Ensure POLICYENGINE_UK_DATA_TOKEN is set."}
    except Exception as e:
        logger.error(f"Error in run_economy_simulation: {e}")
        import traceback; logger.error(traceback.format_exc())
        return {"error": str(e)}


def analyse_microdata(
    entity: str,
    operation: str,
    year: int = 2025,
    reform: Optional[Dict[str, Any]] = None,
    structural_reform: Optional[Dict[str, Any]] = None,
    filters: Optional[Dict[str, Any]] = None,
    columns: Optional[List[str]] = None,
    group_by: Optional[List[str]] = None,
    n: int = 5,
    dataset: str = "frs",
) -> Dict[str, Any]:
    try:
        import pandas as pd

        policy = _build_compiled_policy(reform)
        structural = _build_structural_reform(structural_reform)
        if structural is not None:
            from policyengine_uk_compiled import combine_microdata
            sim = _build_simulation(year, dataset)
            baseline_microdata = sim.run_microdata()
            reform_microdata = sim.run_microdata(policy=policy, structural=structural)
            microdata = combine_microdata(baseline_microdata, reform_microdata)
        else:
            microdata = _get_cached_microdata(year, reform, dataset)

        entity_map = {"persons": microdata.persons, "benunits": microdata.benunits, "households": microdata.households}
        if entity not in entity_map:
            return {"error": f"entity must be one of: persons, benunits, households"}
        df = entity_map[entity].copy()

        weights = microdata.households[["household_id", "weight"]].copy()
        if "household_id" in df.columns and "weight" not in df.columns:
            df = df.merge(weights, on="household_id", how="left")
        elif "weight" not in df.columns:
            df["weight"] = 1.0

        change_pairs = {
            "persons": [("income_tax", "baseline_income_tax", "reform_income_tax"), ("employee_ni", "baseline_employee_ni", "reform_employee_ni"), ("total_income", "baseline_total_income", "reform_total_income")],
            "benunits": [("total_benefits", "baseline_total_benefits", "reform_total_benefits"), ("universal_credit", "baseline_universal_credit", "reform_universal_credit"), ("child_benefit", "baseline_child_benefit", "reform_child_benefit")],
            "households": [("net_income", "baseline_net_income", "reform_net_income"), ("total_tax", "baseline_total_tax", "reform_total_tax"), ("total_benefits", "baseline_total_benefits", "reform_total_benefits")],
        }
        for change_col, base_col, ref_col in change_pairs.get(entity, []):
            if base_col in df.columns and ref_col in df.columns:
                df[f"{change_col}_change"] = df[ref_col] - df[base_col]

        filters_applied = {}
        if filters:
            for col, fval in filters.items():
                if col not in df.columns:
                    return {"error": f"Column '{col}' not found. Available: {list(df.columns)}"}
                filters_applied[col] = fval
                if isinstance(fval, dict):
                    if "min" in fval: df = df[df[col] >= fval["min"]]
                    if "max" in fval: df = df[df[col] <= fval["max"]]
                    if "gt" in fval:  df = df[df[col] > fval["gt"]]
                    if "lt" in fval:  df = df[df[col] < fval["lt"]]
                    if "gte" in fval: df = df[df[col] >= fval["gte"]]
                    if "lte" in fval: df = df[df[col] <= fval["lte"]]
                    if "ne" in fval:  df = df[df[col] != fval["ne"]]
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
            if entity == "persons":
                value_cols = ["age", "gender", "employment_income", "self_employment_income", "baseline_income_tax", "reform_income_tax", "income_tax_change", "baseline_total_income", "reform_total_income", "total_income_change"]
            elif entity == "benunits":
                value_cols = ["baseline_total_benefits", "reform_total_benefits", "total_benefits_change", "baseline_universal_credit", "reform_universal_credit", "baseline_child_benefit", "reform_child_benefit"]
            else:
                value_cols = ["region", "baseline_net_income", "reform_net_income", "net_income_change", "baseline_total_tax", "reform_total_tax", "baseline_total_benefits", "reform_total_benefits"]
            value_cols = [c for c in value_cols if c in df.columns]

        if operation == "sample":
            actual_n = min(n, 20, row_count)
            sample_df = df[value_cols].sample(n=actual_n, random_state=42) if row_count >= actual_n else df[value_cols]
            result = [{k: (None if (isinstance(v, float) and str(v) == "nan") else v) for k, v in row.items()} for row in sample_df.to_dict(orient="records")]
        elif operation == "mean":
            numeric_cols = [c for c in value_cols if pd.api.types.is_numeric_dtype(df[c]) and c != "weight"]
            result = {c: float((df[c] * df["weight"]).sum() / df["weight"].sum()) if df["weight"].sum() > 0 else float(df[c].mean()) for c in numeric_cols}
        elif operation == "sum":
            numeric_cols = [c for c in value_cols if pd.api.types.is_numeric_dtype(df[c]) and c != "weight"]
            result = {c: float((df[c] * df["weight"]).sum()) for c in numeric_cols}
        elif operation == "count":
            result = {"row_count": row_count, "weighted_population": weighted_count}
        elif operation == "describe":
            numeric_cols = [c for c in value_cols if pd.api.types.is_numeric_dtype(df[c]) and c != "weight"]
            result = {c: {"mean": float((df[c] * df["weight"]).sum() / df["weight"].sum()) if df["weight"].sum() > 0 else float(df[c].mean()), "min": float(df[c].min()), "max": float(df[c].max()), "count": int(df[c].count())} for c in numeric_cols}
            for c in [c for c in value_cols if not pd.api.types.is_numeric_dtype(df[c])]:
                result[c] = {str(k): int(v) for k, v in df[c].value_counts().head(10).items()}
        else:
            return {"error": f"Unknown operation '{operation}'. Use: mean, sum, count, sample, describe"}

        dataset_labels = {"frs": "Family Resources Survey", "efrs": "Enhanced FRS", "spi": "Survey of Personal Incomes", "lcfs": "Living Costs and Food Survey", "was": "Wealth and Assets Survey"}
        return {"entity": entity, "operation": operation, "year": year, "dataset": dataset_labels.get(dataset, dataset), "reform_applied": reform is not None, "structural_reform_applied": structural is not None, "filters_applied": filters_applied, "row_count": row_count, "weighted_count": weighted_count, "result": result, "available_columns": all_cols}
    except Exception as e:
        logger.error(f"Error in analyse_microdata: {e}")
        import traceback; logger.error(traceback.format_exc())
        return {"error": str(e)}


def generate_chart(
    chart_type: str, title: str, data: List[Dict[str, Any]], x_field: str, y_fields: List[str],
    x_label: Optional[str] = None, y_label: Optional[str] = None,
    x_format: Optional[str] = None, y_format: Optional[str] = None,
    x_min: Optional[float] = None, x_max: Optional[float] = None,
    y_min: Optional[float] = None, y_max: Optional[float] = None,
    series_labels: Optional[List[str]] = None, series_styles: Optional[List[str]] = None,
    series_curves: Optional[List[str]] = None, subtitle: Optional[str] = None,
    source: Optional[str] = None, arrangement: Optional[str] = None, area_fill: Optional[bool] = None,
) -> Dict[str, Any]:
    try:
        series = []
        for i, y_field in enumerate(y_fields):
            s = {"field": y_field, "label": series_labels[i] if series_labels and i < len(series_labels) else y_field}
            if series_styles and i < len(series_styles): s["lineStyle"] = series_styles[i]
            if series_curves and i < len(series_curves): s["curve"] = series_curves[i]
            series.append(s)

        spec = {
            "type": chart_type, "title": title,
            "x": {"field": x_field, "label": x_label or x_field},
            "y": {"field": y_fields[0] if len(y_fields) == 1 else "value", "label": y_label or (y_fields[0] if len(y_fields) == 1 else "Value")},
            "series": series, "data": data, "showLegend": len(y_fields) > 1, "showGrid": True,
        }
        if x_format: spec["x"]["format"] = x_format
        if y_format: spec["y"]["format"] = y_format
        if x_min is not None: spec["x"]["min"] = x_min
        if x_max is not None: spec["x"]["max"] = x_max
        if y_min is not None: spec["y"]["min"] = y_min
        if y_max is not None: spec["y"]["max"] = y_max
        if subtitle: spec["subtitle"] = subtitle
        if source: spec["source"] = source
        if arrangement and chart_type == "bar": spec["arrangement"] = arrangement
        if area_fill and chart_type == "line": spec["areaFill"] = area_fill

        return {"status": "success", "chart_markdown": f"```chart\n{json.dumps(spec, indent=2)}\n```", "message": "Chart generated. Include the chart_markdown in your response to display it."}
    except Exception as e:
        return {"error": str(e)}


def run_python(code: str) -> Dict[str, Any]:
    """Execute Python code with the PolicyEngine UK compiled interface preloaded.

    The code should assign its final result to a variable called `result`.
    The environment includes the official Python wrapper so runs are easy to
    reproduce outside the chat app.
    """
    import math
    import builtins as _builtins
    _ensure_compiled_package_importable()
    import pandas as pd
    import policyengine_uk_compiled as pe

    from policyengine_uk_compiled import (
        Simulation,
        StructuralReform,
        Parameters,
        aggregate_microdata,
        combine_microdata,
        capabilities,
        ensure_dataset,
    )

    safe_names = (
        "range", "len", "int", "float", "str", "bool", "list", "dict",
        "tuple", "set", "zip", "enumerate", "map", "filter", "sorted",
        "reversed", "min", "max", "sum", "abs", "round", "True", "False",
        "None", "isinstance", "ValueError", "TypeError", "Exception",
        "print", "any", "all", "pow", "divmod", "complex", "type",
        "dir", "hasattr", "getattr",
    )
    safe_builtins = {k: getattr(_builtins, k) for k in safe_names if hasattr(_builtins, k)}

    try:
        import numpy as np
    except ImportError:
        np = None

    output_lines: List[str] = []
    def safe_print(*args, **kwargs):
        output_lines.append(" ".join(str(a) for a in args))

    safe_builtins["print"] = safe_print
    safe_builtins["__import__"] = _safe_import

    allowed_globals: Dict[str, Any] = {
        "__builtins__": safe_builtins,
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
        allowed_globals["np"] = np
        allowed_globals["numpy"] = np

    try:
        exec(code, allowed_globals)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

    result = allowed_globals.get("result", None)

    response: Dict[str, Any] = {}
    if result is not None:
        response["result"] = _json_safe(result)
    if output_lines:
        response["output"] = "\n".join(output_lines)
    if not response:
        response["result"] = None
        response["note"] = "No 'result' variable was set and nothing was printed."
    return response



def _run_generator(code: str) -> Dict[str, Any]:
    """Execute a Python generator snippet that returns a dict of tool kwargs.

    The code must define a `generate()` function that returns a dict.
    Only safe builtins + math are available — no file/network/import access.
    """
    import math
    import builtins as _builtins
    safe_names = (
        "range", "len", "int", "float", "str", "bool", "list", "dict",
        "tuple", "set", "zip", "enumerate", "map", "filter", "sorted",
        "reversed", "min", "max", "sum", "abs", "round", "True", "False",
        "None", "isinstance", "ValueError", "TypeError", "append",
    )
    safe_builtins = {k: getattr(_builtins, k) for k in safe_names if hasattr(_builtins, k)}
    allowed_globals: Dict[str, Any] = {"__builtins__": safe_builtins, "math": math, "json": json}
    exec(code, allowed_globals)
    if "generate" not in allowed_globals:
        raise ValueError("Generator code must define a `generate()` function")
    result = allowed_globals["generate"]()
    if not isinstance(result, dict):
        raise ValueError(f"generate() must return a dict, got {type(result).__name__}")
    return result


def execute_tool(tool_name: str, tool_input: Dict[str, Any]) -> Dict[str, Any]:
    logger.info(f"[TOOLS] Executing {tool_name}")
    tools = {
        "run_python": run_python,
    }
    if tool_name not in tools:
        return {"error": f"Unknown tool: {tool_name}"}
    try:
        # If input contains a generator, execute it to produce the real kwargs
        if "generator" in tool_input:
            logger.info(f"[TOOLS] Running generator for {tool_name}")
            tool_input = _run_generator(tool_input["generator"])
            logger.info(f"[TOOLS] Generator produced keys: {list(tool_input.keys())}")
        result = tools[tool_name](**tool_input)
        logger.info(f"[TOOLS] {tool_name} completed")
        return result
    except Exception as e:
        logger.error(f"[TOOLS] Error in {tool_name}: {e}")
        return {"error": str(e)}


TOOL_DEFINITIONS = [
    {
        "name": "run_python",
        "description": "Execute reproducible Python code using the official PolicyEngine UK compiled interface. The environment preloads `policyengine_uk_compiled` as `pe`, plus `Simulation`, `Parameters`, `StructuralReform`, `aggregate_microdata`, `combine_microdata`, `capabilities`, `ensure_dataset`, `pd`, `np`, `json`, and `math`. Assign the final answer to `result` and use `print()` for intermediate output.",
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python code to execute. Must assign the final answer to `result`. Use the preloaded PolicyEngine interface directly, for example: `sim = Simulation(year=2025)` or `policy = Parameters.model_validate({...})`."},
            },
            "required": ["code"],
        },
    },
]
