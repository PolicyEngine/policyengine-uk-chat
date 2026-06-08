"""Unit tests for the reference.md engine-version drift check.

Covers `_check_reference_engine_drift` (routes/chatbot.py) across matching,
mismatched, missing, and unknown stamps, plus the shared `get_engine_version`
resolver. Pure-logic tests: the engine version resolver is monkeypatched, so
these run without microdata or a live engine. The chatbot import is guarded so
the module skips cleanly where backend deps aren't installed.
"""

import logging

import pytest

chatbot = pytest.importorskip("routes.chatbot")
import tooling.simulations as simulations

DRIFT_LOGGER = "routes.chatbot"


def _doc(version_marker: str) -> str:
    return f"# Reference\n\n{version_marker}\n\n## Body\n"


def test_matching_stamp_logs_info_no_warning(monkeypatch, caplog):
    monkeypatch.setattr(simulations, "get_engine_version", lambda: "0.38.0")
    with caplog.at_level(logging.INFO, logger=DRIFT_LOGGER):
        chatbot._check_reference_engine_drift(_doc("<!-- engine-version: 0.38.0 -->"))
    assert any("matches installed engine 0.38.0" in r.message for r in caplog.records)
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)


def test_mismatched_stamp_warns(monkeypatch, caplog):
    monkeypatch.setattr(simulations, "get_engine_version", lambda: "0.39.0")
    with caplog.at_level(logging.INFO, logger=DRIFT_LOGGER):
        chatbot._check_reference_engine_drift(_doc("<!-- engine-version: 0.38.0 -->"))
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "expected a drift warning"
    assert "built against engine 0.38.0" in warnings[0].message
    assert "installed engine is 0.39.0" in warnings[0].message


def test_missing_stamp_warns(monkeypatch, caplog):
    monkeypatch.setattr(simulations, "get_engine_version", lambda: "0.38.0")
    with caplog.at_level(logging.INFO, logger=DRIFT_LOGGER):
        chatbot._check_reference_engine_drift("# Reference\n\nno marker here\n")
    assert any(
        "no engine-version stamp" in r.message
        for r in caplog.records
        if r.levelno >= logging.WARNING
    )


def test_unknown_stamp_treated_as_unverifiable(monkeypatch, caplog):
    # A doc built where the version couldn't be resolved stamps "unknown".
    # The resolver returns a real version here; we must NOT warn about a
    # phantom "unknown != 0.38.0" mismatch.
    monkeypatch.setattr(simulations, "get_engine_version", lambda: "0.38.0")
    with caplog.at_level(logging.INFO, logger=DRIFT_LOGGER):
        chatbot._check_reference_engine_drift(_doc("<!-- engine-version: unknown -->"))
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings and "stamp is 'unknown'" in warnings[0].message
    assert not any("built against engine" in r.message for r in caplog.records)


def test_unresolvable_installed_version_skips_silently(monkeypatch, caplog):
    monkeypatch.setattr(simulations, "get_engine_version", lambda: None)
    with caplog.at_level(logging.INFO, logger=DRIFT_LOGGER):
        chatbot._check_reference_engine_drift(_doc("<!-- engine-version: 0.38.0 -->"))
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)
    assert not any("matches installed engine" in r.message for r in caplog.records)


def test_empty_doc_is_noop(monkeypatch, caplog):
    monkeypatch.setattr(simulations, "get_engine_version", lambda: "0.38.0")
    with caplog.at_level(logging.INFO, logger=DRIFT_LOGGER):
        chatbot._check_reference_engine_drift("")
    assert caplog.records == []


def test_get_engine_version_returns_string_when_installed():
    pytest.importorskip("policyengine_uk_compiled")
    version = simulations.get_engine_version()
    assert isinstance(version, str) and version
