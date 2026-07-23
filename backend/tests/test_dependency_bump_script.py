"""Unit tests for .github/scripts/dependency_bump.py.

The script drives the dependency-update-pr workflow: it plans exact-pin
bumps for PolicyEngine packages in backend/requirements.txt and opens PRs
for them. These tests cover the planning and rewriting logic with the PyPI
and git/gh seams mocked; nothing here touches the network or runs
subprocesses.
"""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from packaging.version import Version

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / ".github" / "scripts" / "dependency_bump.py"
)
_spec = importlib.util.spec_from_file_location("dependency_bump", _SCRIPT_PATH)
bump = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bump)

REQUIREMENTS = (
    "fastapi\n"
    "policyengine[uk]==0.44.0\n"
    "pandas\n"
    "policyengine-observability[fastapi,httpx]==1.0.0\n"
)


def _plan(content, package, target, crosses_minor):
    return {
        "package": package,
        "match": bump.parse_pin(content, package),
        "target": Version(target),
        "crosses_minor": crosses_minor,
    }


# ── Pin parsing ──────────────────────────────────────────────────────────


def test_parse_pin_reads_version_and_extras():
    match = bump.parse_pin(REQUIREMENTS, "policyengine-observability")
    assert match.group("extras") == "[fastapi,httpx]"
    assert match.group("version") == "1.0.0"


def test_parse_pin_ignores_comments_ranges_and_other_packages():
    content = (
        "# policyengine==0.1.0\n"
        "policyengine\n"
        "policyengine>=0.44.0,<0.45\n"
        "policyengine-uk==1.0.0\n"
    )
    assert bump.parse_pin(content, "policyengine") is None


def test_pinned_packages_finds_all_policyengine_pins():
    assert bump.pinned_packages(REQUIREMENTS) == [
        "policyengine",
        "policyengine-observability",
    ]


# ── Pin rewriting ────────────────────────────────────────────────────────


def test_bumped_line_rewrites_exact_pin():
    match = bump.parse_pin(REQUIREMENTS, "policyengine")
    assert bump.bumped_line(match, Version("0.44.2")) == (
        "policyengine[uk]==0.44.2"
    )


def test_bumped_line_keeps_extras():
    match = bump.parse_pin(REQUIREMENTS, "policyengine-observability")
    assert bump.bumped_line(match, Version("1.3.1")) == (
        "policyengine-observability[fastapi,httpx]==1.3.1"
    )


# ── Bump planning ────────────────────────────────────────────────────────


def test_plan_explicit_version_not_newer_than_pin_is_noop(monkeypatch):
    monkeypatch.setattr(bump, "pypi_releases", lambda package: [Version("0.44.0")])
    assert bump.plan_bumps(REQUIREMENTS, "policyengine", "0.44.0") == []


def test_plan_explicit_patch_version_within_minor(monkeypatch):
    monkeypatch.setattr(bump, "pypi_releases", lambda package: [Version("0.44.2")])
    plans = bump.plan_bumps(REQUIREMENTS, "policyengine", "0.44.2")
    assert [(p["target"], p["crosses_minor"]) for p in plans] == [
        (Version("0.44.2"), False)
    ]


def test_plan_explicit_version_crossing_minor_needs_review(monkeypatch):
    monkeypatch.setattr(bump, "pypi_releases", lambda package: [Version("0.46.0")])
    plans = bump.plan_bumps(REQUIREMENTS, "policyengine", "0.46.0")
    assert [(p["target"], p["crosses_minor"]) for p in plans] == [
        (Version("0.46.0"), True)
    ]


def test_plan_waits_for_pypi_propagation(monkeypatch):
    calls = []

    def releases(package):
        calls.append(package)
        return [Version("0.44.2")] if len(calls) > 2 else []

    monkeypatch.setattr(bump, "pypi_releases", releases)
    monkeypatch.setattr(bump.time, "sleep", lambda seconds: None)
    plans = bump.plan_bumps(REQUIREMENTS, "policyengine", "0.44.2")
    assert len(calls) == 3
    assert plans[0]["target"] == Version("0.44.2")


def test_plan_exits_when_version_never_reaches_pypi(monkeypatch):
    monkeypatch.setattr(bump, "pypi_releases", lambda package: [])
    monkeypatch.setattr(bump, "PYPI_WAIT_ATTEMPTS", 2)
    monkeypatch.setattr(bump.time, "sleep", lambda seconds: None)
    with pytest.raises(SystemExit):
        bump.plan_bumps(REQUIREMENTS, "policyengine", "0.44.2")


def test_plan_discovery_proposes_patch_and_cross_minor_separately(monkeypatch):
    # A newer cross-minor release must not hide the newest same-minor patch:
    # the patch is proposed for auto-merge, the cross-minor jump for review.
    monkeypatch.setattr(
        bump,
        "pypi_releases",
        lambda package: [
            Version("0.44.1"),
            Version("0.44.2"),
            Version("0.46.0"),
            Version("0.47.0"),
        ],
    )
    plans = bump.plan_bumps(REQUIREMENTS, "policyengine", "")
    assert [(p["target"], p["crosses_minor"]) for p in plans] == [
        (Version("0.44.2"), False),
        (Version("0.47.0"), True),
    ]


def test_plan_discovery_only_cross_minor_releases(monkeypatch):
    monkeypatch.setattr(
        bump, "pypi_releases", lambda package: [Version("1.2.0"), Version("1.3.1")]
    )
    plans = bump.plan_bumps(REQUIREMENTS, "policyengine-observability", "")
    assert [(p["target"], p["crosses_minor"]) for p in plans] == [
        (Version("1.3.1"), True)
    ]


def test_plan_discovery_current_pin_is_noop(monkeypatch):
    monkeypatch.setattr(bump, "pypi_releases", lambda package: [Version("0.44.0")])
    assert bump.plan_bumps(REQUIREMENTS, "policyengine", "") == []


def test_plan_missing_pin_is_noop(monkeypatch):
    monkeypatch.setattr(
        bump, "pypi_releases", lambda package: pytest.fail("should not hit PyPI")
    )
    assert bump.plan_bumps("fastapi\n", "policyengine", "") == []


# ── PyPI release listing ─────────────────────────────────────────────────


def test_pypi_releases_skips_yanked_empty_and_invalid(monkeypatch):
    payload = {
        "releases": {
            "1.0.0": [{"yanked": False}],
            "1.1.0": [{"yanked": True}],
            "1.2.0": [],
            "1.2.1": [{"yanked": True}, {"yanked": False}],
            "not-a-version": [{"yanked": False}],
        }
    }

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def read(self):
            return json.dumps(payload)

    monkeypatch.setattr(
        bump.urllib.request, "urlopen", lambda url, timeout=30: FakeResponse()
    )
    assert sorted(bump.pypi_releases("policyengine-anything")) == [
        Version("1.0.0"),
        Version("1.2.1"),
    ]


# ── PR opening (git/gh mocked) ───────────────────────────────────────────


@pytest.fixture
def pr_sandbox(tmp_path, monkeypatch):
    """Chdir into a fake repo root and record git/gh invocations."""
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "requirements.txt").write_text(REQUIREMENTS)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(bump, "branch_exists", lambda branch: False)

    commands = []

    def fake_run(*args, capture=False):
        commands.append(args)
        return SimpleNamespace(stdout="https://github.com/PolicyEngine/x/pull/999\n")

    monkeypatch.setattr(bump, "run", fake_run)

    merge_calls = []

    def fake_subprocess_run(cmd, capture_output=False, text=False):
        merge_calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(bump.subprocess, "run", fake_subprocess_run)
    return SimpleNamespace(root=tmp_path, commands=commands, merge_calls=merge_calls)


def test_open_pr_rewrites_pin_and_requests_automerge(pr_sandbox):
    plan = _plan(REQUIREMENTS, "policyengine", "0.44.2", crosses_minor=False)
    bump.open_pr(REQUIREMENTS, plan)

    written = (pr_sandbox.root / "backend" / "requirements.txt").read_text()
    assert "policyengine[uk]==0.44.2" in written
    # Other pins are untouched.
    assert "policyengine-observability[fastapi,httpx]==1.0.0" in written

    branch = "auto/bump-policyengine-0.44.2"
    assert ("git", "switch", "-c", branch, "main") in pr_sandbox.commands
    assert ("git", "push", "-u", "origin", branch) in pr_sandbox.commands
    assert ("git", "switch", "main") in pr_sandbox.commands

    pr_create = next(c for c in pr_sandbox.commands if c[:3] == ("gh", "pr", "create"))
    assert pr_create[pr_create.index("--title") + 1] == (
        "chore: bump policyengine to 0.44.2"
    )
    assert pr_sandbox.merge_calls[0][:5] == ["gh", "pr", "merge", "--auto", "--squash"]


def test_open_pr_cross_minor_skips_automerge(pr_sandbox):
    plan = _plan(REQUIREMENTS, "policyengine-observability", "1.3.1", crosses_minor=True)
    bump.open_pr(REQUIREMENTS, plan)

    written = (pr_sandbox.root / "backend" / "requirements.txt").read_text()
    assert "policyengine-observability[fastapi,httpx]==1.3.1" in written

    pr_create = next(c for c in pr_sandbox.commands if c[:3] == ("gh", "pr", "create"))
    assert pr_create[pr_create.index("--title") + 1] == (
        "chore: bump policyengine-observability to 1.3.1 (crosses minor version)"
    )
    assert "crosses a minor version boundary" in pr_create[pr_create.index("--body") + 1]
    assert pr_sandbox.merge_calls == []


def test_open_pr_skips_existing_branch(pr_sandbox, monkeypatch):
    monkeypatch.setattr(bump, "branch_exists", lambda branch: True)
    plan = _plan(REQUIREMENTS, "policyengine", "0.44.2", crosses_minor=False)
    bump.open_pr(REQUIREMENTS, plan)

    assert pr_sandbox.commands == []
    unchanged = (pr_sandbox.root / "backend" / "requirements.txt").read_text()
    assert unchanged == REQUIREMENTS


# ── Entry point ──────────────────────────────────────────────────────────


def test_main_rejects_version_without_package(monkeypatch):
    monkeypatch.setenv("VERSION", "1.2.3")
    monkeypatch.delenv("PACKAGE", raising=False)
    with pytest.raises(SystemExit):
        bump.main()


def test_main_dry_run_sweeps_all_pins_without_opening_prs(tmp_path, monkeypatch):
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "requirements.txt").write_text(REQUIREMENTS)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DRY_RUN", "1")
    monkeypatch.delenv("PACKAGE", raising=False)
    monkeypatch.delenv("VERSION", raising=False)

    swept = []

    def releases(package):
        swept.append(package)
        return [Version("99.0.0")]

    monkeypatch.setattr(bump, "pypi_releases", releases)
    monkeypatch.setattr(
        bump, "open_pr", lambda content, plan: pytest.fail("dry run must not open PRs")
    )
    bump.main()
    assert swept == ["policyengine", "policyengine-observability"]
