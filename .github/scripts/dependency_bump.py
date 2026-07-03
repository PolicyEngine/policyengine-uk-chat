#!/usr/bin/env python3
"""Open dependency bump PRs for policyengine-* pins in backend/requirements.txt.

Invoked by .github/workflows/dependency-update-pr.yml on:
- repository_dispatch (package-release) from an upstream release pipeline,
  carrying the exact package and version just published to PyPI;
- workflow_dispatch, for manual bumps or sweeps;
- a daily schedule, as a fallback sweep for dispatches lost to outages.

Pins are exact (`==`), so every version reaching production has been through
a PR and CI — there is no in-range drift between releases. Behaviour per
package:
- a release within the pinned minor version is a patch update: the PR has
  auto-merge requested so it lands once CI is green;
- a release at/above the next minor is left for human review, so a
  potentially breaking upstream release surfaces as a visible PR instead of
  an untested deploy.

Version discovery reads PyPI's full `releases` map, never `info.version`,
which only reports the newest release overall and would hide a same-minor
patch behind a newer cross-minor release.

Environment:
  PACKAGE  optional; a single package to bump (empty = all policyengine-*
           pins found in backend/requirements.txt).
  VERSION  optional; exact target version (requires PACKAGE). Empty means
           "newest suitable release on PyPI".
  DRY_RUN  set to 1 to print planned bumps without touching git or GitHub.
  GH_TOKEN required by `gh` unless DRY_RUN=1.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request

from packaging.version import InvalidVersion, Version

REQ_PATH = "backend/requirements.txt"
PIN_RE = re.compile(
    r"^(?P<name>policyengine-[a-z0-9-]+)"
    r"(?P<extras>\[[^\]]*\])?"
    r"==(?P<version>[0-9]+(?:\.[0-9]+)*)$"
)
# How long to wait for a dispatched version to appear on PyPI's JSON API
# (publish and JSON propagation can lag the dispatch by a little).
PYPI_WAIT_ATTEMPTS = 20
PYPI_WAIT_SECONDS = 15


def run(*args: str, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), check=True, text=True, capture_output=capture)


def pypi_releases(package: str) -> list[Version]:
    url = f"https://pypi.org/pypi/{package}/json"
    with urllib.request.urlopen(url, timeout=30) as response:
        data = json.load(response)
    versions = []
    for raw, files in data["releases"].items():
        if not files or all(f.get("yanked") for f in files):
            continue
        try:
            versions.append(Version(raw))
        except InvalidVersion:
            continue
    return versions


def parse_pin(content: str, package: str) -> re.Match | None:
    for line in content.splitlines():
        match = PIN_RE.match(line.strip())
        if match and match.group("name") == package:
            return match
    return None


def pinned_packages(content: str) -> list[str]:
    return [
        match.group("name")
        for line in content.splitlines()
        if (match := PIN_RE.match(line.strip()))
    ]


def plan_bumps(content: str, package: str, explicit_version: str) -> list[dict]:
    """Return a list of {package, match, target, crosses_minor} bump plans."""
    match = parse_pin(content, package)
    if match is None:
        print(f"{package}: no == pin found in {REQ_PATH}; skipping.")
        return []
    current = Version(match.group("version"))
    # Auto-merge policy boundary: releases within the pinned minor are safe
    # patch updates; anything at/above the next minor needs human review.
    next_minor = Version(f"{current.major}.{current.minor + 1}")

    if explicit_version:
        target = Version(explicit_version)
        if target <= current:
            print(f"{package}: {target} is not newer than the {current} pin; nothing to do.")
            return []
        for attempt in range(PYPI_WAIT_ATTEMPTS):
            if target in pypi_releases(package):
                break
            print(f"{package}: {target} not on PyPI yet; waiting {PYPI_WAIT_SECONDS}s...")
            time.sleep(PYPI_WAIT_SECONDS)
        else:
            print(f"{package}: {target} never appeared on PyPI; giving up.", file=sys.stderr)
            sys.exit(1)
        crosses = target >= next_minor
        return [{"package": package, "match": match, "target": target, "crosses_minor": crosses}]

    releases = pypi_releases(package)
    plans = []
    patch = [v for v in releases if v > current and v < next_minor]
    if patch:
        plans.append(
            {"package": package, "match": match, "target": max(patch), "crosses_minor": False}
        )
    beyond = [v for v in releases if v >= next_minor]
    if beyond:
        plans.append(
            {"package": package, "match": match, "target": max(beyond), "crosses_minor": True}
        )
    if not plans:
        print(f"{package}: pin =={current} is current.")
    return plans


def bumped_line(match: re.Match, target: Version) -> str:
    return f"{match.group('name')}{match.group('extras') or ''}=={target}"


def branch_exists(branch: str) -> bool:
    probe = subprocess.run(
        ["git", "ls-remote", "--exit-code", "--heads", "origin", branch],
        capture_output=True,
        text=True,
    )
    return probe.returncode == 0


def open_pr(content: str, plan: dict) -> None:
    package, match, target = plan["package"], plan["match"], plan["target"]
    crosses_minor = plan["crosses_minor"]
    old_line = match.group(0)
    new_line = bumped_line(match, target)
    branch = f"auto/bump-{package}-{target}"

    if branch_exists(branch):
        print(f"{package}: branch {branch} already exists; skipping.")
        return

    run("git", "switch", "-c", branch, "main")
    try:
        with open(REQ_PATH, "w") as handle:
            handle.write(content.replace(old_line, new_line))
        run("git", "add", REQ_PATH)
        run("git", "commit", "-m", f"chore: bump {package} to {target}")
        run("git", "push", "-u", "origin", branch)

        title = f"chore: bump {package} to {target}"
        if crosses_minor:
            title += " (crosses minor version)"
        body_lines = [
            f"Automated bump of the `{package}` pin in `{REQ_PATH}`:",
            "",
            f"```diff\n-{old_line}\n+{new_line}\n```",
            "",
        ]
        if crosses_minor:
            body_lines += [
                f"⚠️ **{target} crosses a minor version boundary** — upstream may have "
                "shipped breaking changes. Review the upstream changelog and CI results "
                "before merging; auto-merge is intentionally not requested.",
            ]
        else:
            body_lines += [
                "The release stays within the pinned minor version. Auto-merge is "
                "requested, so this lands once CI passes (if the repository allows "
                "auto-merge); the merge to `main` then triggers the normal deploy "
                "workflow.",
            ]
        pr = run(
            "gh", "pr", "create",
            "--base", "main",
            "--head", branch,
            "--title", title,
            "--body", "\n".join(body_lines),
            capture=True,
        )
        pr_url = pr.stdout.strip().splitlines()[-1]
        print(f"{package}: opened {pr_url}")

        if not crosses_minor:
            enable = subprocess.run(
                ["gh", "pr", "merge", "--auto", "--squash", pr_url],
                capture_output=True,
                text=True,
            )
            if enable.returncode == 0:
                print(f"{package}: auto-merge enabled on {pr_url}")
            else:
                print(
                    f"{package}: could not enable auto-merge ({enable.stderr.strip()}); "
                    "merge manually once CI is green."
                )
    finally:
        run("git", "switch", "main")


def main() -> None:
    package = os.environ.get("PACKAGE", "").strip()
    version = os.environ.get("VERSION", "").strip()
    dry_run = os.environ.get("DRY_RUN", "").strip() == "1"
    if version and not package:
        print("VERSION was given without PACKAGE; refusing to guess.", file=sys.stderr)
        sys.exit(1)

    with open(REQ_PATH) as handle:
        content = handle.read()

    packages = [package] if package else pinned_packages(content)
    if not packages:
        print(f"No policyengine-* pins found in {REQ_PATH}; nothing to do.")
        return

    for name in packages:
        for plan in plan_bumps(content, name, version if name == package else ""):
            marker = " (crosses minor)" if plan["crosses_minor"] else ""
            print(f"{name}: planned bump to {plan['target']}{marker}")
            if dry_run:
                continue
            open_pr(content, plan)


if __name__ == "__main__":
    main()
