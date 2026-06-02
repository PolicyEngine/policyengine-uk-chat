"""PolicyEngine UK compiled-package and simulation helpers."""

from pathlib import Path
import sys
from typing import Any, Dict


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


def build_simulation(year: int, dataset: str = "frs"):
    """Build a compiled PolicyEngine UK Simulation."""
    ensure_compiled_package_importable()
    from policyengine_uk_compiled import Simulation

    return Simulation(year=year, dataset=dataset)


def get_capabilities() -> Dict[str, Any]:
    ensure_compiled_package_importable()
    from policyengine_uk_compiled import capabilities

    return capabilities()

