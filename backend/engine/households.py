"""Illustrative household input normalization."""

from typing import Any, Dict, List, Tuple

from engine.simulations import ensure_compiled_package_importable


def build_household_frames(
    person: List[Dict[str, Any]],
    benunit: List[Dict[str, Any]],
    household: List[Dict[str, Any]],
) -> Tuple[Any, Any, Any]:
    ensure_compiled_package_importable()
    import pandas as pd
    from policyengine_uk_compiled import BENUNIT_DEFAULTS, HOUSEHOLD_DEFAULTS, PERSON_DEFAULTS

    def fill_defaults(records, defaults):
        return pd.DataFrame([{**defaults, **rec} for rec in records])

    hh_id_map = {rec["household_id"]: i for i, rec in enumerate(household)}
    bu_id_map = {rec["benunit_id"]: i for i, rec in enumerate(benunit)}
    person = [
        {
            **rec,
            "person_id": i,
            "benunit_id": bu_id_map[rec["benunit_id"]],
            "household_id": hh_id_map[rec["household_id"]],
        }
        for i, rec in enumerate(person)
    ]
    benunit = [
        {
            **rec,
            "benunit_id": bu_id_map[rec["benunit_id"]],
            "household_id": hh_id_map[rec["household_id"]],
        }
        for rec in benunit
    ]
    household = [{**rec, "household_id": hh_id_map[rec["household_id"]]} for rec in household]

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

    if "person_ids" not in benunits_df.columns or (
        benunits_df["person_ids"] == BENUNIT_DEFAULTS.get("person_ids", 0)
    ).all():
        bu_to_persons = persons_df.groupby("benunit_id")["person_id"].apply(
            lambda ids: ",".join(str(i) for i in ids)
        )
        benunits_df["person_ids"] = (
            benunits_df["benunit_id"].map(bu_to_persons).fillna(benunits_df["benunit_id"].astype(str))
        )
    if "benunit_ids" not in households_df.columns or (
        households_df["benunit_ids"] == HOUSEHOLD_DEFAULTS.get("benunit_ids", 0)
    ).all():
        hh_to_benunits = benunits_df.groupby("household_id")["benunit_id"].apply(
            lambda ids: ",".join(str(i) for i in ids)
        )
        households_df["benunit_ids"] = (
            households_df["household_id"].map(hh_to_benunits).fillna(households_df["household_id"].astype(str))
        )
    if "person_ids" not in households_df.columns or (
        households_df["person_ids"] == HOUSEHOLD_DEFAULTS.get("person_ids", 0)
    ).all():
        hh_to_persons = persons_df.groupby("household_id")["person_id"].apply(
            lambda ids: ",".join(str(i) for i in ids)
        )
        households_df["person_ids"] = (
            households_df["household_id"].map(hh_to_persons).fillna(households_df["household_id"].astype(str))
        )

    return persons_df, benunits_df, households_df

