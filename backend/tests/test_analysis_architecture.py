from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.pool import StaticPool
from sqlmodel import create_engine

from analysis.persistence import ensure_analysis_tables
from analysis import turn_service
from analysis.store import AnalysisStore
from analysis_helpers import analysis_store_boundary, typed_clock


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ANALYSIS_ROOT = REPOSITORY_ROOT / "backend" / "analysis"
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
TEMPORARY_CHAT_IMPORTERS: set[str] = set()


def _import_layers(path: Path) -> frozenset[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    layers: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            layers.update(_import_layer(alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            layers.add(_import_layer(node.module))
    return frozenset(layers)


def _import_layer(module: str) -> str:
    """Return the application layer targeted by an absolute or relative import."""
    parts = module.split(".")
    if parts[0] == "backend" and len(parts) > 1:
        return parts[1]
    return parts[0]


@dataclass(frozen=True)
class ImportBoundary:
    """A permitted dependency relationship for a defined source scope."""

    source_root: Path
    forbidden_layers: frozenset[str]
    allowed_importers: frozenset[str] = frozenset()

    def importers(self, paths: tuple[Path, ...] | None = None) -> frozenset[str]:
        source_paths = paths or tuple(self.source_root.glob("*.py"))
        return frozenset(
            path.name
            for path in source_paths
            if _import_layers(path) & self.forbidden_layers
        )

    def violations(self, paths: tuple[Path, ...] | None = None) -> dict[str, list[str]]:
        source_paths = paths or tuple(self.source_root.glob("*.py"))
        return {
            path.name: sorted(_import_layers(path) & self.forbidden_layers)
            for path in source_paths
            if path.name not in self.allowed_importers
            and _import_layers(path) & self.forbidden_layers
        }


DOMAIN_BOUNDARY = ImportBoundary(
    source_root=ANALYSIS_ROOT,
    forbidden_layers=frozenset(FORBIDDEN_DOMAIN_IMPORTS),
)
ANALYSIS_TO_EVALUATION_BOUNDARY = ImportBoundary(
    source_root=ANALYSIS_ROOT,
    forbidden_layers=frozenset({"eval"}),
)
ANALYSIS_TO_CHAT_BOUNDARY = ImportBoundary(
    source_root=ANALYSIS_ROOT,
    forbidden_layers=frozenset({"chat"}),
    allowed_importers=frozenset(TEMPORARY_CHAT_IMPORTERS),
)


def test_import_layer_recognizes_backend_namespaces():
    assert _import_layer("backend.chat.projector") == "chat"
    assert _import_layer("backend.eval.runner") == "eval"
    assert _import_layer("analysis.lifecycle") == "analysis"


def test_domain_modules_do_not_import_external_effect_layers():
    paths = tuple(ANALYSIS_ROOT / filename for filename in DOMAIN_MODULES)

    assert DOMAIN_BOUNDARY.violations(paths) == {}


def test_analysis_never_imports_evaluation_code():
    assert ANALYSIS_TO_EVALUATION_BOUNDARY.violations() == {}


def test_analysis_does_not_import_chat_modules():
    assert ANALYSIS_TO_CHAT_BOUNDARY.importers() == frozenset(
        TEMPORARY_CHAT_IMPORTERS
    )
    assert ANALYSIS_TO_CHAT_BOUNDARY.violations() == {}


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


def test_turn_service_uses_request_compilation_facade_once():
    source = inspect.getsource(turn_service._run_turn)

    assert source.count("dependencies.request_compiler.compile") == 1
    for forbidden in (
        "reduce_semantic_update",
        "dependencies.binder",
        "dependencies.compiler",
    ):
        assert forbidden not in source


def test_turn_service_uses_execution_facade_once():
    source = inspect.getsource(turn_service._run_turn)

    assert source.count("dependencies.execution_engine.execute") == 1
    for forbidden in (
        "_execute_standard_plan",
        "_execute_exploratory_plan",
        "dependencies.standard_executor",
        "dependencies.exploratory_executor",
    ):
        assert forbidden not in source


def test_turn_service_supplies_the_plan_claim_transition_to_the_store():
    source = inspect.getsource(turn_service._run_turn)

    assert source.count("store.commit_plan_claim") == 1
    assert "store.claim_plan(" not in source
