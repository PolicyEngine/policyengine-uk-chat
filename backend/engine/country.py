"""Country profile seam: which tax-benefit country this chat instance serves.

One deployment serves one country. The active profile is resolved once
from the ``CHAT_COUNTRY`` environment variable (default ``uk``), and every
country-specific engine binding — the policyengine.py country module, the
fixed society dataset, and the household geography identifiers — flows
from it. A second deployment of the same codebase can therefore serve
another country by setting ``CHAT_COUNTRY``, without touching the UK
pathway.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
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


@dataclass(frozen=True)
class CountryProfile:
    """Country-specific engine bindings for one chat deployment."""

    id: str
    """policyengine.py country module name (``pe.<id>``) and manifest key."""

    dataset: DatasetConfig
    """The deployment's fixed society dataset."""

    household_geo_ids: tuple[str, ...]
    """Valid values for the household ``country`` geography input."""

    dataset_notes: str
    """Display note attached to the resolved dataset spec."""


UK_PROFILE = CountryProfile(
    id="uk",
    dataset=DatasetConfig(
        uri=(
            "hf://policyengine/policyengine-uk-data-private/"
            "enhanced_frs_2024_25.h5@1.56.13"
        )
    ),
    household_geo_ids=(
        "ENGLAND",
        "NORTHERN_IRELAND",
        "SCOTLAND",
        "WALES",
    ),
    dataset_notes="UK Chat's fixed society dataset.",
)

PROFILES: dict[str, CountryProfile] = {UK_PROFILE.id: UK_PROFILE}


@lru_cache(maxsize=1)
def active_country_profile() -> CountryProfile:
    """Resolve this deployment's country profile from CHAT_COUNTRY."""

    country = os.environ.get("CHAT_COUNTRY", UK_PROFILE.id)
    profile = PROFILES.get(country)
    if profile is None:
        supported = ", ".join(sorted(PROFILES))
        raise RuntimeError(
            f"CHAT_COUNTRY={country!r} has no country profile; "
            f"supported values: {supported}."
        )
    return profile
