# Deployment and operations

The backend deploys to [Modal](https://modal.com) as an ASGI app. CI is handled
by GitHub Actions workflows in `.github/workflows/`.

## Modal backend

`modal_app.py` defines the deployment:

- **Image** — Debian slim + Python 3.13, with `libpq-dev`/`gcc` and the Python
  dependencies (FastAPI, `pydantic-ai[anthropic]`, `policyengine-uk-compiled`,
  `policyengine_uk`, Supabase, Stripe, `slowapi`, …).
- **Engine pre-load** — `_preload_engine()` runs at build time and bakes a warm
  `Simulation` into the image snapshot, so cold starts are fast.
- **Reference rebuild** — after copying `backend/` into the image, the build runs
  `python scripts/build_reference.py` so the deployed `reference.md` matches the
  Modal-installed engine version (mirrors the Docker build).
- **Function config** — 2 CPUs, 4 GiB memory, 600 s timeout, up to 10 containers,
  `eu` region, 100 concurrent inputs per container.
- **Secrets** — pulled from a Modal secret named `policyengine-uk-chat-secrets`
  (override via `POLICYENGINE_UK_CHAT_MODAL_SECRET_NAME`).

Deploy manually with:

```bash
modal deploy modal_app.py
```

## GitHub Actions workflows

```{list-table}
:header-rows: 1
:widths: 35 65

* - Workflow
  - Trigger / purpose
* - `deploy.yml`
  - On push to `main` (or manual dispatch): deploy the backend to Modal.
    Concurrency-guarded so deploys don't overlap.
* - `pr-beta-deploy.yml`
  - On PR open/sync/reopen/ready/close: stand up (and tear down) a beta preview
    for the pull request, commenting the preview URL.
* - `redeploy-on-package-update.yml`
  - Daily cron (06:00 UTC) + manual: poll PyPI for a new
    `policyengine-uk-compiled` release and redeploy if one shipped — keeping the
    engine and its version-stamped `reference.md` current.
```

## Engine version drift

Because the agent reasons against `reference.md`, that file must match the engine
actually executing the code. Two mechanisms keep them aligned:

1. **Build-time regeneration** — both the Docker and Modal images run
   `scripts/build_reference.py` after installing the engine.
2. **Scheduled redeploy** — `redeploy-on-package-update.yml` ships a new
   deployment when a new engine version is published.

Confirm the live engine version any time with `GET /version`.

## Documentation site (this book)

This Jupyter Book is built and published to GitHub Pages by
`.github/workflows/docs.yml`. On every push to `main` that touches `docs/`, the
workflow installs `docs/requirements.txt`, runs `jupyter-book build docs/`, and
publishes `docs/_build/html` to GitHub Pages.

The published site lives at:

> <https://policyengine.github.io/policyengine-uk-chat/>

To build it locally:

```bash
pip install -r docs/requirements.txt
jupyter-book build docs/
open docs/_build/html/index.html
```
