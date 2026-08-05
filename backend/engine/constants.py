"""Shared engine constants.

The country-specific values here are aliases of the active country
profile (see ``engine.country``). Under the default ``CHAT_COUNTRY=uk``
they resolve to the historical UK constants unchanged; call sites keep
importing these names.
"""

from engine.country import DatasetConfig, active_country_profile

__all__ = ["DatasetConfig", "UK_CHAT_DATASET", "HOUSEHOLD_COUNTRY_IDS"]

UK_CHAT_DATASET = active_country_profile().dataset

HOUSEHOLD_COUNTRY_IDS = active_country_profile().household_geo_ids
