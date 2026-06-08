"""PolicyEngine UK compiled-package and simulation helpers."""

from pathlib import Path
import sys
from typing import Any, Dict, Optional


DATASET_LABELS = {
    "frs": "Family Resources Survey",
    "efrs": "Enhanced FRS",
    "spi": "Survey of Personal Incomes",
    "lcfs": "Living Costs and Food Survey",
    "was": "Wealth and Assets Survey",
}


def ensure_compiled_package_importable() -> None:
    """Make the local policyengine_uk_compiled package importable in dev setups."""
    try:
        import policyengine_uk_compiled  # noqa: F401
        return
    except ModuleNotFoundError:
        pass

    repo_parent = Path(__file__).resolve().parents[3]
    candidates = [
        repo_parent / "policyengine-uk-rust" / "interfaces" / "python",
        repo_parent / "policyengine-uk-rust-codex-debug-issue" / "interfaces" / "python",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            candidate_str = str(candidate)
            if candidate_str not in sys.path:
                sys.path.insert(0, candidate_str)
            try:
                import policyengine_uk_compiled  # noqa: F401
                return
            except ModuleNotFoundError:
                continue

    raise ModuleNotFoundError(
        "policyengine_uk_compiled is not importable. Install the package or make sure a local "
        "policyengine-uk-rust checkout with interfaces/python is available."
    )


def get_engine_version() -> Optional[str]:
    """Resolve the installed engine version, or None if it can't be determined.

    Single source of truth for both the build-time stamp
    (`scripts/build_reference.py`) and the load-time drift check
    (`routes/chatbot.py`), so the two can't drift apart.

    Resolves via distribution metadata (handling both the hyphen and underscore
    spellings of the dist name). Ensures the local rust-checkout is importable
    first so this works in dev, not just in the packaged image. Returns None
    rather than raising; callers treat None as "unable to verify". Note the
    engine exposes no module `__version__`, so distribution metadata is the only
    reliable source.
    """
    import importlib.metadata

    try:
        ensure_compiled_package_importable()
    except ModuleNotFoundError:
        return None

    for dist in ("policyengine-uk-compiled", "policyengine_uk_compiled"):
        try:
            return importlib.metadata.version(dist)
        except importlib.metadata.PackageNotFoundError:
            continue
    return None


def build_simulation(year: int, dataset: str = "frs"):
    """Build a compiled PolicyEngine UK Simulation."""
    ensure_compiled_package_importable()
    from policyengine_uk_compiled import Simulation

    return Simulation(year=year, dataset=dataset)


def get_capabilities() -> Dict[str, Any]:
    ensure_compiled_package_importable()
    from policyengine_uk_compiled import capabilities

    return capabilities()

