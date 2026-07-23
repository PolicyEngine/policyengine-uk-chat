"""Shared engine constants."""

DEFAULT_UK_DATASET = "enhanced_frs_2024_25"
DEFAULT_UK_DATASET_URI = (
    "hf://policyengine/policyengine-uk-data-private/"
    "enhanced_frs_2024_25.h5@1.56.13"
)

HOUSEHOLD_COUNTRY_IDS = (
    "ENGLAND",
    "NORTHERN_IRELAND",
    "SCOTLAND",
    "WALES",
)

# policyengine.py's certified standard UK default is currently
# ``populace_uk_2023``. UK Chat defaults to Enhanced FRS for continuity with
# existing analysis workflows; this can be changed to the standard default if
# the chat runtime should follow the bundle default exactly.
STANDARD_POLICYENGINE_UK_DATASET = "populace_uk_2023"

DATASET_LABELS = {
    DEFAULT_UK_DATASET: "Enhanced FRS 2024-25",
    STANDARD_POLICYENGINE_UK_DATASET: "PolicyEngine UK standard certified dataset",
    "frs_2023_24": "Family Resources Survey 2023-24",
}

ROW_LEVEL_RESTRICTED_DATASETS = {
    DEFAULT_UK_DATASET,
    STANDARD_POLICYENGINE_UK_DATASET,
    "frs_2023_24",
}


def is_row_level_restricted_dataset(name: str) -> bool:
    """Return whether a dataset is restricted to aggregate analysis."""

    return name.startswith("enhanced_frs_") or name in ROW_LEVEL_RESTRICTED_DATASETS
