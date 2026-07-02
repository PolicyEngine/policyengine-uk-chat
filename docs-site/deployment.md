# Deployment and operations

The backend deploys to [Modal](https://modal.com) as an ASGI app, driven by CI
under `.github/workflows/`. The frontend deploys on [Vercel](https://vercel.com).
This page covers how the backend image is built, how the workflows ship it, and
how the engine version is kept in sync with the agent's API reference.

## Modal backend

`backend/modal_app.py` defines the deployed application: it wraps the FastAPI
app (`api/main.py`) and bakes the compiled engine into the image snapshot.

- **Image.** `modal.Image.debian_slim(python_version="3.13")` with `libpq-dev`
  and `gcc`, plus the `backend/requirements.txt` dependencies — FastAPI, the
  Anthropic SDK, `policyengine-uk-compiled`, Supabase, Stripe, `slowapi`,
  `policyengine-observability`, and the rest.
- **Engine pre-load.** `_preload_engine()` runs at build time: it imports the
  compiled `Simulation`, builds a `year=2024` simulation, and fetches baseline
  parameters. This bakes a warm engine into the image snapshot so cold starts
  are fast.
- **Reference rebuild.** After the engine is installed, the build runs
  `python engine/reference.py`, so the deployed `reference.md` and
  `scope_descriptor.md` match the Modal-installed engine version (this mirrors
  the Docker build). See [The engine layer](backend/engine.md).
- **Function config.** 2 CPUs, 4096 MiB memory, a 600s timeout,
  `max_containers=10`, region `"eu"`, and `@modal.concurrent(max_inputs=100)`.
- **Configurable app and secret names.**
  `POLICYENGINE_UK_CHAT_MODAL_APP_NAME` (default `policyengine-uk-chat`) and
  `POLICYENGINE_UK_CHAT_MODAL_SECRET_NAME` (default
  `policyengine-uk-chat-secrets`).

Deploy manually with:

```bash
modal deploy modal_app.py
```

## Docker image

`backend/Dockerfile` builds the image used for local development and as a
reference for the Modal build:

- **Base.** `ghcr.io/astral-sh/uv:python3.13-bookworm`.
- **Reference rebuild.** Runs `python engine/reference.py` at build time to
  generate `reference.md` and `scope_descriptor.md` against the installed
  engine.
- **Entrypoint.** Starts the app with `uvicorn api.main:app` on port `8080`
  inside the container, mapped to `8001` locally by `docker-compose`.

## GitHub Actions workflows

```{list-table}
:header-rows: 1

* - Workflow
  - Trigger and purpose
* - `deploy.yml`
  - On push to `main` (or manual `workflow_dispatch`): sync secrets to Modal and
    run `modal deploy modal_app.py`. Concurrency-guarded.
* - `pr-beta-deploy.yml`
  - On `pull_request` (opened / synchronize / reopened / ready_for_review /
    closed): stand up a per-PR Modal beta backend (and tear it down on close),
    commenting the preview URL on the PR.
* - `tests.yml`
  - On `pull_request` to `main`: run backend pytest (`make test-backend`) and the
    frontend build.
* - `redeploy-on-package-update.yml`
  - Daily cron (`0 6 * * *` UTC) plus manual: poll PyPI for a new
    `policyengine-uk-compiled` release and redeploy if one shipped, keeping the
    engine and its version-stamped `reference.md` current.
* - `docs.yml`
  - On push to `main` touching `docs-site/**` (or the workflow file itself), or
    manual: build this Jupyter Book and publish it to GitHub Pages.
```

## Engine version drift

Because the agent reasons against `reference.md`, that file must describe the
engine version that is actually executing the code. Two mechanisms keep them
aligned:

1. **Build-time regeneration** — both the Docker and Modal images run
   `python engine/reference.py` after installing the engine, so the reference is
   regenerated against the engine that ships in the image.
2. **Scheduled redeploy** — `redeploy-on-package-update.yml` ships a new
   deployment whenever a new engine version is published on PyPI.

You can confirm the live engine version at any time with `GET /version`.

```{important}
The deployed `reference.md` must always match the `policyengine-uk-compiled`
version actually running in the container. Regenerating it at build time and
redeploying on every engine release is what upholds that invariant — never edit
`reference.md` by hand.
```

## Documentation site (this book)

This Jupyter Book is built and published to GitHub Pages by
`.github/workflows/docs.yml`. On every push to `main` that touches `docs-site/`,
the workflow installs `docs-site/requirements.txt`, runs
`jupyter-book build docs-site/`, and publishes `docs-site/_build/html` to GitHub
Pages. Once Pages is enabled (Settings → Pages → Source: "GitHub Actions"), the
published site lives at
<https://policyengine.github.io/policyengine-uk-chat/>.

To build the book locally:

```bash
pip install -r docs-site/requirements.txt
jupyter-book build docs-site/
open docs-site/_build/html/index.html
```

```{note}
The AI-facing engineering guidance and eval harness live separately under
`docs/engineering/skills/` (referenced by `CLAUDE.md` and `AGENTS.md`). That
material is **not** part of this published book.
```
