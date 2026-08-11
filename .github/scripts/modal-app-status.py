#!/usr/bin/env python3

import json
import sys


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: modal-app-status.py APP_NAME")

    app_name = sys.argv[1]
    apps = json.load(sys.stdin)
    if not isinstance(apps, list):
        raise SystemExit("modal app list --json did not return a list")

    matches = [app for app in apps if app.get("description") == app_name]
    if not matches:
        print("missing")
    elif all(app.get("state") == "stopped" for app in matches):
        print("stopped")
    else:
        print("active")


if __name__ == "__main__":
    main()
