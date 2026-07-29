#!/usr/bin/env bash

set -euo pipefail

git config user.name "policyengine[bot]"
git config user.email "policyengine[bot]@users.noreply.github.com"
python .github/scripts/dependency_bump.py
