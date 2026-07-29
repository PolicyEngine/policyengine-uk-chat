#!/usr/bin/env python3
"""Write the Vercel preview URL derived from a PR branch to GitHub outputs."""

from __future__ import annotations

import os
from pathlib import Path
import re


def slugify_branch(branch_name: str) -> str:
    """Return the branch slug used by Vercel preview hostnames."""
    value = re.sub(r"[^a-z0-9]+", "-", branch_name.lower())
    value = re.sub(r"^-+|-+$", "", value)
    return re.sub(r"-{2,}", "-", value)


def preview_frontend_url(branch_name: str) -> str:
    """Return the Vercel preview URL for a branch."""
    slug = slugify_branch(branch_name)
    return f"https://policyengine-uk-chat-git-{slug}-policy-engine.vercel.app"


def main() -> None:
    branch_name = os.environ["BRANCH_NAME"]
    output_path = Path(os.environ["GITHUB_OUTPUT"])
    slug = slugify_branch(branch_name)
    with output_path.open("a") as output:
        output.write(f"branch_slug={slug}\n")
        output.write(f"frontend_url={preview_frontend_url(branch_name)}\n")


if __name__ == "__main__":
    main()
