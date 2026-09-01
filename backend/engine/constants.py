"""Shared engine constants."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DatasetConfig:
    """One manifest dataset name and its derived display metadata."""

    name: str

    @property
    def label(self) -> str:
        enhanced_frs = re.fullmatch(r"enhanced_frs_(\d{4})_(\d{2})", self.name)
        if enhanced_frs is None:
            return self.name
        return f"Enhanced FRS {enhanced_frs.group(1)}-{enhanced_frs.group(2)}"


UK_CHAT_DATASET = DatasetConfig(
    name="enhanced_frs_2024_25",
)

HOUSEHOLD_COUNTRY_IDS = (
    "ENGLAND",
    "NORTHERN_IRELAND",
    "SCOTLAND",
    "WALES",
)
