from __future__ import annotations

import ast
import inspect
from pathlib import Path

from sqlalchemy.pool import StaticPool
from sqlmodel import create_engine

from analysis.persistence import ensure_analysis_tables
from analysis import coordinator
from analysis.store import AnalysisStore
from analysis_helpers import analysis_store_boundary, typed_clock


ANALYSIS_ROOT = Path("backend/analysis")
DOMAIN_MODULES = (
    "common.py",
    "models.py",
    "reducer.py",
    "lifecycle.py",
)
FORBIDDEN_DOMAIN_IMPORTS = {
    "anthropic",
    "billing",
    "chat",
    "eval",
    "sqlalchemy",
    "sqlmodel",
    "tools",
}
TEMPORARY_CHAT_IMPORTERS = {
    "coordinator.py",
}


def _import_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.partition(".")[0])
    return roots


def test_domain_modules_do_not_import_external_effect_layers():
    violations = {
        filename: sorted(_import_roots(ANALYSIS_ROOT / filename) & FORBIDDEN_DOMAIN_IMPORTS)
        for filename in DOMAIN_MODULES
        if _import_roots(ANALYSIS_ROOT / filename) & FORBIDDEN_DOMAIN_IMPORTS
    }

    assert violations == {}


def test_analysis_never_imports_evaluation_code():
    violations = [
        path.name
        for path in ANALYSIS_ROOT.glob("*.py")
        if "eval" in _import_roots(path)
    ]

    assert violations == []


def test_chat_imports_are_limited_to_known_compatibility_modules():
    importers = {
        path.name
        for path in ANALYSIS_ROOT.glob("*.py")
        if "chat" in _import_roots(path)
    }

    assert importers == TEMPORARY_CHAT_IMPORTERS


def test_shared_clock_and_persistence_fakes_satisfy_typed_boundaries():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ensure_analysis_tables(engine)
    store = analysis_store_boundary(engine)

    assert typed_clock()() is not None
    assert isinstance(store, AnalysisStore)


def test_coordinator_uses_request_compilation_facade_once():
    source = inspect.getsource(coordinator.run_analysis_turn)

    assert source.count("dependencies.request_compiler.compile") == 1
    for forbidden in (
        "reduce_semantic_update",
        "dependencies.binder",
        "dependencies.compiler",
    ):
        assert forbidden not in source


def test_coordinator_uses_execution_facade_once():
    source = inspect.getsource(coordinator.run_analysis_turn)

    assert source.count("dependencies.execution_engine.execute") == 1
    for forbidden in (
        "execute_standard_plan",
        "execute_exploratory_plan",
        "dependencies.standard_executor",
        "dependencies.exploratory_executor",
    ):
        assert forbidden not in source
