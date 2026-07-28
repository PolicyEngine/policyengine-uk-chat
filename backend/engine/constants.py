"""Shared engine constants."""

import re
from dataclasses import dataclass
from pathlib import PurePosixPath


@dataclass(frozen=True)
class DatasetConfig:
    """One fixed dataset reference and its derived display metadata."""

    uri: str

    @property
    def name(self) -> str:
        path = self.uri.rsplit("@", 1)[0]
        filename = PurePosixPath(path).name
        return filename.removesuffix(".h5")

    @property
    def label(self) -> str:
        enhanced_frs = re.fullmatch(r"enhanced_frs_(\d{4})_(\d{2})", self.name)
        if enhanced_frs is None:
            return self.name
        return f"Enhanced FRS {enhanced_frs.group(1)}-{enhanced_frs.group(2)}"


UK_CHAT_DATASET = DatasetConfig(
    uri=(
        "hf://policyengine/policyengine-uk-data-private/"
        "enhanced_frs_2024_25.h5@1.56.13"
    )
)

HOUSEHOLD_COUNTRY_IDS = (
    "ENGLAND",
    "NORTHERN_IRELAND",
    "SCOTLAND",
    "WALES",
)
