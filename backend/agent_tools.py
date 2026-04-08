"""
Agent tools for the microsim chatbot.
Wraps compiled PolicyEngine UK simulations and utility operations.
"""

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Microdata cache
# ---------------------------------------------------------------------------
_microdata_cache: Dict[tuple, Any] = {}
_MAX_CACHE = 4


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


def _build_structural_reform(code: str):
    """Compile user-supplied Python code into a StructuralReform."""
    from policyengine_uk_compiled import StructuralReform
    import pandas as _pd
    import numpy as _np
    import math as _math
    safe_builtins = {k: __builtins__[k] for k in (
        "range", "len", "int", "float", "str", "bool", "list", "dict",
        "tuple", "set", "enumerate", "zip", "map", "filter", "sorted",
        "min", "max", "sum", "abs", "round", "print", "isinstance", "type",
        "None", "True", "False",
    ) if k in ((__builtins__ if isinstance(__builtins__, dict) else vars(__builtins__)))}
    ns: dict = {"pd": _pd, "np": _np, "math": _math, "__builtins__": safe_builtins}
    exec(compile(code, "<structural_reform>", "exec"), ns)
    pre_fn = ns.get("pre")
    post_fn = ns.get("post")
    if pre_fn is None and post_fn is None:
        raise ValueError("structural_reform code must define at least a pre() or post() function")
    return StructuralReform(pre=pre_fn, post=post_fn)


def get_capabilities() -> Dict[str, Any]:
    try:
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
    from policyengine_uk_compiled import Simulation
    return Simulation(year=year, dataset=dataset)


def run_economy_simulation(year: int = 2025, reform: Optional[Dict[str, Any]] = None, dataset: str = "efrs", structural=None) -> Dict[str, Any]:
    try:
        policy = _build_compiled_policy(reform)

        if structural is not None:
            from policyengine_uk_compiled.structural import aggregate_microdata
            baseline_md = _get_cached_microdata(year, None, dataset)
            reform_md = _get_cached_microdata(year, reform, dataset, structural=structural)
            baseline_result = aggregate_microdata(baseline_md.persons, baseline_md.benunits, baseline_md.households, year)
            reform_result = aggregate_microdata(reform_md.persons, reform_md.benunits, reform_md.households, year)
        else:
            sim = _build_simulation(year, dataset)
            baseline_result = sim.run()
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
            "hbai_incomes": reform_result.hbai_incomes.model_dump(),
            "baseline_poverty": baseline_result.baseline_poverty.model_dump(),
            "reform_poverty": reform_result.reform_poverty.model_dump(),
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
    filters: Optional[Dict[str, Any]] = None,
    columns: Optional[List[str]] = None,
    group_by: Optional[List[str]] = None,
    n: int = 5,
    dataset: str = "efrs",
    structural=None,
) -> Dict[str, Any]:
    try:
        import pandas as pd

        microdata = _get_cached_microdata(year, reform, dataset, structural=structural)

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
        return {"entity": entity, "operation": operation, "year": year, "dataset": dataset_labels.get(dataset, dataset), "reform_applied": reform is not None, "filters_applied": filters_applied, "row_count": row_count, "weighted_count": weighted_count, "result": result, "available_columns": all_cols}
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
    """Execute Python code in a sandboxed environment with math/numpy available.

    The code should assign its final result to a variable called `result`.
    Only safe builtins, math, and numpy are available — no file/network/import access.
    """
    import math
    import builtins as _builtins

    safe_names = (
        "range", "len", "int", "float", "str", "bool", "list", "dict",
        "tuple", "set", "zip", "enumerate", "map", "filter", "sorted",
        "reversed", "min", "max", "sum", "abs", "round", "True", "False",
        "None", "isinstance", "ValueError", "TypeError", "print",
        "any", "all", "pow", "divmod", "complex",
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

    allowed_globals: Dict[str, Any] = {
        "__builtins__": safe_builtins,
        "math": math,
    }
    if np is not None:
        allowed_globals["np"] = np
        allowed_globals["numpy"] = np

    try:
        exec(code, allowed_globals)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

    result = allowed_globals.get("result", None)

    # Convert numpy types to JSON-safe Python types
    def to_json_safe(obj):
        if np is not None:
            if isinstance(obj, np.ndarray):
                return obj.tolist()
            if isinstance(obj, (np.integer,)):
                return int(obj)
            if isinstance(obj, (np.floating,)):
                return float(obj)
            if isinstance(obj, (np.bool_,)):
                return bool(obj)
        if isinstance(obj, dict):
            return {k: to_json_safe(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [to_json_safe(v) for v in obj]
        return obj

    response: Dict[str, Any] = {}
    if result is not None:
        response["result"] = to_json_safe(result)
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
        "get_capabilities": get_capabilities,
        "get_baseline_parameters": get_baseline_parameters,
        "calculate_household": calculate_household,
        "run_economy_simulation": run_economy_simulation,
        "analyse_microdata": analyse_microdata,
        "generate_chart": generate_chart,
        "run_python": run_python,
    }
    if tool_name not in tools:
        return {"error": f"Unknown tool: {tool_name}"}
    try:
        # Compile structural_reform code into a StructuralReform object
        if "structural_reform" in tool_input:
            tool_input = dict(tool_input)
            structural_code = tool_input.pop("structural_reform")
            tool_input["structural"] = _build_structural_reform(structural_code)
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
        "name": "get_capabilities",
        "description": "Returns a structured description of the engine's capabilities: available datasets, locally cached years per dataset, programmes modelled, available microdata columns, and key notes. The response is pre-populated at the start of every conversation, but you can call this again if needed.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_baseline_parameters",
        "description": "Get the full set of current-law policy parameter values for a given fiscal year. Call this BEFORE constructing a reform to see available field names and current values.",
        "input_schema": {
            "type": "object",
            "properties": {"year": {"type": "integer", "description": "Fiscal year (e.g. 2025 = 2025/26). Default: 2025.", "default": 2025}},
        },
    },
    {
        "name": "calculate_household",
        "description": "Calculate tax and benefit outcomes for one or more custom household situations, comparing baseline vs reform. Every output appears as baseline_<var> and reform_<var>. Batch multiple scenarios in ONE call. For many households (>3), use the 'generator' field instead of writing out arrays by hand.",
        "input_schema": {
            "type": "object",
            "properties": {
                "generator": {"type": "string", "description": "Python code defining a generate() function that returns a dict with keys: person, benunit, household, and optionally year and reform. Use this instead of writing large arrays by hand. Example: 'def generate():\\n    persons, benunits, households = [], [], []\\n    for i in range(20):\\n        income = 10000 + i * 5000\\n        persons.append({\"person_id\": i, \"benunit_id\": i, \"household_id\": i, \"age\": 35, \"employment_income\": income})\\n        benunits.append({\"benunit_id\": i, \"household_id\": i})\\n        households.append({\"household_id\": i})\\n    return {\"person\": persons, \"benunit\": benunits, \"household\": households, \"year\": 2025}'"},
                "person": {"type": "array", "items": {"type": "object", "properties": {"person_id": {"type": "integer"}, "benunit_id": {"type": "integer"}, "household_id": {"type": "integer"}, "age": {"type": "integer"}, "employment_income": {"type": "number"}, "self_employment_income": {"type": "number"}, "private_pension_income": {"type": "number"}, "state_pension": {"type": "number"}, "savings_interest": {"type": "number"}, "is_in_scotland": {"type": "boolean"}}, "required": ["person_id", "benunit_id", "household_id", "age"]}},
                "benunit": {"type": "array", "items": {"type": "object", "properties": {"benunit_id": {"type": "integer"}, "household_id": {"type": "integer"}, "rent_monthly": {"type": "number"}, "is_lone_parent": {"type": "boolean"}}, "required": ["benunit_id", "household_id"]}},
                "household": {"type": "array", "items": {"type": "object", "properties": {"household_id": {"type": "integer"}, "region": {"type": "string"}, "rent_annual": {"type": "number"}, "council_tax_annual": {"type": "number"}}, "required": ["household_id"]}},
                "year": {"type": "integer", "default": 2025},
                "reform": {"type": "object", "description": "Optional policy reform dict. Example: {\"income_tax\": {\"personal_allowance\": 15000}}"},
            },
        },
    },
    {
        "name": "run_economy_simulation",
        "description": "Run an economy-wide UK microsimulation. Returns budgetary impact, program breakdown, decile impacts, winners/losers, and caseloads.",
        "input_schema": {
            "type": "object",
            "properties": {
                "year": {"type": "integer", "description": "Fiscal year. Default: 2025 (current FY).", "default": 2025},
                "reform": {"type": "object", "description": "Optional policy reform. Top-level keys: income_tax, national_insurance, universal_credit, child_benefit, state_pension, pension_credit, benefit_cap, housing_benefit, tax_credits, scottish_child_payment."},
                "dataset": {"type": "string", "enum": ["efrs", "frs", "spi", "lcfs", "was"], "description": "Dataset. 'efrs' (default, gold standard): Enhanced FRS with wealth and consumption. 'frs': Family Resources Survey, use for pre-2023 years or cross-checking. 'spi': Survey of Personal Incomes, person-level only (tax/NI, no benefits), best for high earners. 'lcfs': consumption/VAT analysis. 'was': wealth/asset analysis.", "default": "efrs"},
                "structural_reform": {"type": "string", "description": "Python code defining pre(year, persons, benunits, households) and/or post(year, persons, benunits, households) hooks. Both return (persons, benunits, households). pandas (pd) and numpy (np) available. Use for reforms that can't be expressed as parameter changes (e.g. UBI, new benefits, structural population changes)."},
            },
        },
    },
    {
        "name": "analyse_microdata",
        "description": "Run an economy-wide simulation and analyse the resulting person/benunit/household microdata. Automatically computes change columns (net_income_change, income_tax_change, total_benefits_change). Results are cached — repeated calls with the same year/reform/dataset are instant. Use to answer: 'Who loses?', 'Average age of losers?', 'Show me an example household that benefits'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "entity": {"type": "string", "enum": ["persons", "benunits", "households"]},
                "operation": {"type": "string", "enum": ["mean", "sum", "count", "sample", "describe", "crosstab"], "description": "Aggregation. Use 'crosstab' with group_by=[row, col] for pivot tables."},
                "year": {"type": "integer", "default": 2025},
                "reform": {"type": "object"},
                "filters": {"type": "object", "description": "Filter rows. Keys are column names. Values: exact, list, range {min/max}, or comparison {gt/lt/gte/lte/ne}. E.g. {\"net_income_change\": {\"lt\": 0}}"},
                "columns": {"type": "array", "items": {"type": "string"}},
                "group_by": {"type": "array", "items": {"type": "string"}, "description": "Group results by these columns (works with sum/mean/count/crosstab)."},
                "n": {"type": "integer", "default": 5},
                "dataset": {"type": "string", "enum": ["efrs", "frs", "spi", "lcfs", "was"], "description": "Dataset. 'efrs' (default, gold standard). 'frs' for pre-2023 or cross-checking. 'spi' (entity must be 'persons'). 'lcfs' for consumption. 'was' for wealth.", "default": "efrs"},
                "structural_reform": {"type": "string", "description": "Same as run_economy_simulation. Pass the same string to guarantee both tools use the same microdata run."},
            },
            "required": ["entity", "operation"],
        },
    },
    {
        "name": "generate_chart",
        "description": "Generate an interactive chart from data. Use 'step' curve for policy rates/thresholds; 'linear' for simulated income/tax. Title must be an active finding, not a generic label. ALWAYS set explicit x.min/x.max and y.min/y.max appropriate to your data — never leave axis ranges unset (d3 defaults to nonsensical ranges like 0–2500 for year axes).",
        "input_schema": {
            "type": "object",
            "properties": {
                "chart_type": {"type": "string", "enum": ["line", "bar", "area"]},
                "title": {"type": "string"},
                "data": {"type": "array", "items": {"type": "object"}},
                "x_field": {"type": "string"},
                "y_fields": {"type": "array", "items": {"type": "string"}},
                "x_label": {"type": "string"},
                "y_label": {"type": "string"},
                "x_format": {"type": "string", "enum": ["currency", "percent", "percent_decimal", "number", "compact", "year"]},
                "y_format": {"type": "string", "enum": ["currency", "percent", "percent_decimal", "number", "compact", "year"]},
                "x_min": {"type": "number", "description": "Explicit x-axis minimum. Always set for time-series (set to first year in data) and income axes (set to 0)."},
                "x_max": {"type": "number", "description": "Explicit x-axis maximum. Always set to last year in data or highest income value."},
                "y_min": {"type": "number", "description": "Explicit y-axis minimum. Default to 0 unless the data is naturally bounded above zero with small variation."},
                "y_max": {"type": "number", "description": "Explicit y-axis maximum. Set slightly above the highest data value."},
                "series_labels": {"type": "array", "items": {"type": "string"}},
                "series_styles": {"type": "array", "items": {"type": "string", "enum": ["solid", "dashed", "dotted"]}},
                "series_curves": {"type": "array", "items": {"type": "string", "enum": ["step", "linear", "smooth"]}},
                "source": {"type": "string"},
                "arrangement": {"type": "string", "enum": ["grouped", "stacked"]},
                "area_fill": {"type": "boolean"},
            },
            "required": ["chart_type", "title", "data", "x_field", "y_fields"],
        },
    },
    {
        "name": "run_python",
        "description": "Execute Python code for data processing, maths, and analysis. ALWAYS use this instead of doing arithmetic in your head — even for simple calculations. numpy is available as `np`. Assign the final answer to a variable called `result`. You can also use `print()` for intermediate output. No file or network access. 30-second time limit.",
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python code to execute. Must assign final answer to `result`. numpy available as `np`. Example: 'import numpy as np\\ncpi = np.array([100, 102, 105])\\nresult = list(np.diff(cpi) / cpi[:-1] * 100)'"},
            },
            "required": ["code"],
        },
    },
]
