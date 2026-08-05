"""Country profile seam tests."""

import pytest

from engine import constants
from engine.country import PROFILES, UK_PROFILE, active_country_profile


@pytest.fixture(autouse=True)
def _reset_profile_cache():
    active_country_profile.cache_clear()
    yield
    active_country_profile.cache_clear()


def test_default_profile_is_uk(monkeypatch):
    monkeypatch.delenv("CHAT_COUNTRY", raising=False)

    profile = active_country_profile()

    assert profile is UK_PROFILE
    assert profile.id == "uk"


def test_uk_profile_matches_historical_constants():
    assert constants.UK_CHAT_DATASET.uri == UK_PROFILE.dataset.uri
    assert constants.UK_CHAT_DATASET.name == "enhanced_frs_2024_25"
    assert constants.UK_CHAT_DATASET.label == "Enhanced FRS 2024-25"
    assert constants.HOUSEHOLD_COUNTRY_IDS == (
        "ENGLAND",
        "NORTHERN_IRELAND",
        "SCOTLAND",
        "WALES",
    )


def test_explicit_uk_selection(monkeypatch):
    monkeypatch.setenv("CHAT_COUNTRY", "uk")

    assert active_country_profile() is UK_PROFILE


def test_unknown_country_raises_with_supported_values(monkeypatch):
    monkeypatch.setenv("CHAT_COUNTRY", "atlantis")

    with pytest.raises(RuntimeError, match="atlantis.*supported values: uk"):
        active_country_profile()


def test_profile_registry_keys_match_profile_ids():
    assert all(key == profile.id for key, profile in PROFILES.items())
