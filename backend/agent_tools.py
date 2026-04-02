"""
Agent tools for the microsim chatbot.
Wraps compiled PolicyEngine UK simulations and utility operations.
"""

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


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
    }
    kwargs = {}
    for program, fields in reform.items():
        if program not in param_cls_map:
            raise ValueError(f"Unknown reform program '{program}'. Valid: {list(param_cls_map)}")
        if not isinstance(fields, dict):
            raise ValueError(f"Reform program '{program}' must be a dict, got {type(fields).__name__}")
        cls = param_cls_map[program]
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
    year: int = 2023,
    reform: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    try:
        import pandas as pd
        from policyengine_uk_compiled import Simulation, PERSON_DEFAULTS, BENUNIT_DEFAULTS, HOUSEHOLD_DEFAULTS

        def fill_defaults(records, defaults):
            return pd.DataFrame([{**defaults, **rec} for rec in records])

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


def run_economy_simulation(year: int = 2023, reform: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    try:
        from policyengine_uk_compiled import Simulation
        policy = _build_compiled_policy(reform)
        sim = Simulation(year=year)
        result = sim.run(policy=policy)
        return {
            "fiscal_year": result.fiscal_year,
            "budgetary_impact": result.budgetary_impact.model_dump(),
            "program_breakdown": result.program_breakdown.model_dump(),
            "decile_impacts": [d.model_dump() for d in result.decile_impacts],
            "winners_losers": result.winners_losers.model_dump(),
            "caseloads": result.caseloads.model_dump(),
            "avg_hbai_net_income": result.avg_hbai_net_income,
        }
    except FileNotFoundError as e:
        return {"error": "FRS microdata not available", "detail": str(e), "hint": "Ensure POLICYENGINE_UK_DATA_TOKEN is set."}
    except Exception as e:
        logger.error(f"Error in run_economy_simulation: {e}")
        import traceback; logger.error(traceback.format_exc())
        return {"error": str(e)}


def analyse_microdata(
    entity: str,
    operation: str,
    year: int = 2023,
    reform: Optional[Dict[str, Any]] = None,
    filters: Optional[Dict[str, Any]] = None,
    columns: Optional[List[str]] = None,
    n: int = 5,
) -> Dict[str, Any]:
    try:
        import pandas as pd
        from policyengine_uk_compiled import Simulation

        policy = _build_compiled_policy(reform)
        sim = Simulation(year=year)
        microdata = sim.run_microdata(policy=policy)

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

        return {"entity": entity, "operation": operation, "year": year, "reform_applied": reform is not None, "filters_applied": filters_applied, "row_count": row_count, "weighted_count": weighted_count, "result": result, "available_columns": all_cols}
    except Exception as e:
        logger.error(f"Error in analyse_microdata: {e}")
        import traceback; logger.error(traceback.format_exc())
        return {"error": str(e)}


def generate_chart(
    chart_type: str, title: str, data: List[Dict[str, Any]], x_field: str, y_fields: List[str],
    x_label: Optional[str] = None, y_label: Optional[str] = None,
    x_format: Optional[str] = None, y_format: Optional[str] = None,
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
        if subtitle: spec["subtitle"] = subtitle
        if source: spec["source"] = source
        if arrangement and chart_type == "bar": spec["arrangement"] = arrangement
        if area_fill and chart_type == "line": spec["areaFill"] = area_fill

        return {"status": "success", "chart_markdown": f"```chart\n{json.dumps(spec, indent=2)}\n```", "message": "Chart generated. Include the chart_markdown in your response to display it."}
    except Exception as e:
        return {"error": str(e)}


def compute(operation: str, data: List[float], data2: Optional[List[float]] = None) -> Dict[str, Any]:
    try:
        if not data:
            return {"error": "Empty data array"}
        if operation == "diff":
            result = [data[i+1] - data[i] for i in range(len(data)-1)]
        elif operation == "pct_change":
            result = [((data[i+1]-data[i])/data[i]*100) if data[i] != 0 else 0 for i in range(len(data)-1)]
        elif operation == "cumsum":
            total = 0; result = []
            for x in data: total += x; result.append(total)
        elif operation == "mean":
            result = sum(data)/len(data)
        elif operation == "sum":
            result = sum(data)
        elif operation == "min":
            result = min(data)
        elif operation == "max":
            result = max(data)
        elif operation in ("divide", "multiply", "subtract", "add", "marginal_rate"):
            if not data2 or len(data) != len(data2):
                return {"error": f"data2 required and must match data length for {operation}"}
            if operation == "divide":
                result = [a/b if b != 0 else 0 for a, b in zip(data, data2)]
            elif operation == "multiply":
                result = [a*b for a, b in zip(data, data2)]
            elif operation == "subtract":
                result = [a-b for a, b in zip(data, data2)]
            elif operation == "add":
                result = [a+b for a, b in zip(data, data2)]
            elif operation == "marginal_rate":
                if len(data) < 2:
                    return {"error": "Need at least 2 data points for marginal_rate"}
                result = [(data[i+1]-data[i])/(data2[i+1]-data2[i])*100 if (data2[i+1]-data2[i]) != 0 else 0 for i in range(len(data)-1)]
        else:
            return {"error": f"Unknown operation: {operation}"}
        return {"result": result}
    except Exception as e:
        return {"error": str(e)}



def execute_tool(tool_name: str, tool_input: Dict[str, Any]) -> Dict[str, Any]:
    logger.info(f"[TOOLS] Executing {tool_name}")
    tools = {
        "get_baseline_parameters": get_baseline_parameters,
        "calculate_household": calculate_household,
        "run_economy_simulation": run_economy_simulation,
        "analyse_microdata": analyse_microdata,
        "generate_chart": generate_chart,
        "compute": compute,
    }
    if tool_name not in tools:
        return {"error": f"Unknown tool: {tool_name}"}
    try:
        result = tools[tool_name](**tool_input)
        logger.info(f"[TOOLS] {tool_name} completed")
        return result
    except Exception as e:
        logger.error(f"[TOOLS] Error in {tool_name}: {e}")
        return {"error": str(e)}


TOOL_DEFINITIONS = [
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
        "description": "Calculate tax and benefit outcomes for one or more custom household situations, comparing baseline vs reform. Every output appears as baseline_<var> and reform_<var>. Batch multiple scenarios in ONE call.",
        "input_schema": {
            "type": "object",
            "properties": {
                "person": {"type": "array", "items": {"type": "object", "properties": {"person_id": {"type": "integer"}, "benunit_id": {"type": "integer"}, "household_id": {"type": "integer"}, "age": {"type": "integer"}, "employment_income": {"type": "number"}, "self_employment_income": {"type": "number"}, "private_pension_income": {"type": "number"}, "state_pension": {"type": "number"}, "savings_interest": {"type": "number"}, "is_in_scotland": {"type": "boolean"}}, "required": ["person_id", "benunit_id", "household_id", "age"]}},
                "benunit": {"type": "array", "items": {"type": "object", "properties": {"benunit_id": {"type": "integer"}, "household_id": {"type": "integer"}, "rent_monthly": {"type": "number"}, "is_lone_parent": {"type": "boolean"}}, "required": ["benunit_id", "household_id"]}},
                "household": {"type": "array", "items": {"type": "object", "properties": {"household_id": {"type": "integer"}, "region": {"type": "string"}, "rent_annual": {"type": "number"}, "council_tax_annual": {"type": "number"}}, "required": ["household_id"]}},
                "year": {"type": "integer", "default": 2023},
                "reform": {"type": "object", "description": "Optional policy reform dict. Example: {\"income_tax\": {\"personal_allowance\": 15000}}"},
            },
            "required": ["person", "benunit", "household"],
        },
    },
    {
        "name": "run_economy_simulation",
        "description": "Run an economy-wide UK microsimulation over the full FRS population. Returns budgetary impact, program breakdown, decile impacts, winners/losers, and caseloads. Microdata available 1994–2023.",
        "input_schema": {
            "type": "object",
            "properties": {
                "year": {"type": "integer", "description": "Fiscal year. Microdata available 1994–2023. Default: 2023.", "default": 2023},
                "reform": {"type": "object", "description": "Optional policy reform. Top-level keys: income_tax, national_insurance, universal_credit, child_benefit, state_pension, pension_credit, benefit_cap, housing_benefit, tax_credits, scottish_child_payment."},
            },
        },
    },
    {
        "name": "analyse_microdata",
        "description": "Run an economy-wide simulation and analyse the resulting person/benunit/household microdata. Automatically computes change columns (net_income_change, income_tax_change, total_benefits_change). Use to answer: 'Who loses?', 'Average age of losers?', 'Show me an example household that benefits'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "entity": {"type": "string", "enum": ["persons", "benunits", "households"]},
                "operation": {"type": "string", "enum": ["mean", "sum", "count", "sample", "describe"]},
                "year": {"type": "integer", "default": 2023},
                "reform": {"type": "object"},
                "filters": {"type": "object", "description": "Filter rows. Keys are column names. Values: exact, list, range {min/max}, or comparison {gt/lt/gte/lte/ne}. E.g. {\"net_income_change\": {\"lt\": 0}}"},
                "columns": {"type": "array", "items": {"type": "string"}},
                "n": {"type": "integer", "default": 5},
            },
            "required": ["entity", "operation"],
        },
    },
    {
        "name": "generate_chart",
        "description": "Generate an interactive chart from data. Use 'step' curve for policy rates/thresholds; 'linear' for simulated income/tax. Title must be an active finding, not a generic label.",
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
        "name": "compute",
        "description": "Perform mathematical operations on arrays of numbers. Always use this for arithmetic rather than computing values yourself.",
        "input_schema": {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["diff", "pct_change", "cumsum", "mean", "sum", "min", "max", "divide", "multiply", "subtract", "add", "marginal_rate"]},
                "data": {"type": "array", "items": {"type": "number"}},
                "data2": {"type": "array", "items": {"type": "number"}},
            },
            "required": ["operation", "data"],
        },
    },
]
