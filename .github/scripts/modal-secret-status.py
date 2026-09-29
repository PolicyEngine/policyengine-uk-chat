#!/usr/bin/env python3

import json
import sys


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: modal-secret-status.py SECRET_NAME")

    secret_name = sys.argv[1]
    secrets = json.load(sys.stdin)
    if not isinstance(secrets, list):
        raise SystemExit("modal secret list --json did not return a list")
    # Fail rather than report "missing" if the CLI's JSON shape changes;
    # "missing" makes cleanup skip the schema.
    if not all(isinstance(secret, dict) and "name" in secret for secret in secrets):
        raise SystemExit("modal secret list --json entries have no 'name' key")

    if any(secret.get("name") == secret_name for secret in secrets):
        print("present")
    else:
        print("missing")


if __name__ == "__main__":
    main()
