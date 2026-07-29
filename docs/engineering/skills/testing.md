# Testing

Use this skill whenever adding, moving, or reviewing tests.

See `test-eval-architecture.md` for the boundary between deterministic tests,
scripted model contracts, live-model evals, and the recommended CI checks.

## Layout

- Backend tests live under `backend/tests/`.
- Frontend unit and component tests live beside their source under
  `frontend/src/`; the frontend check runs Vitest with coverage before the
  production build.
- Keep fixtures in `backend/tests/conftest.py` only when they are broadly useful
  across backend tests. Prefer named fixtures over broad autouse fixtures when
  only a subset of tests needs isolation.
- Do not let tests require live Anthropic access by default. Live model tests
  must be gated behind `RUN_LIVE_ANTHROPIC_TESTS=1` and `ANTHROPIC_API_KEY`.

## Dependency Boundaries

- Unit tests should mock network, database, and model-client seams unless they
  are explicitly marked as live/integration tests.
- Tests that depend on the policyengine.py UK packages should skip cleanly when
  they are not installed locally, while CI should install backend
  dependencies before running the full backend suite.
- Conversation-table tests should use the named isolated table fixture rather
  than a shared developer database.
- The deterministic `/chat` integration smoke test must keep the model boundary
  controlled while exercising the real HTTP/SSE route, tool dispatcher, and
  policyengine.py calculation. Browser automation is not required for the
  current simple UX.

## Commands

Install backend test dependencies separately from runtime dependencies:

```bash
python -m pip install -r backend/requirements.txt \
  -r backend/requirements-test.txt
```

Before handing off backend changes, run the focused backend tests that cover the
changed code. For broader verification, use:

```bash
make test-backend
```

Before handing off frontend changes, run:

```bash
make test-frontend
```

For changes spanning both sides, run:

```bash
make test
```

Every pull request also runs the four deterministic eval boundaries as separate
checks:

```bash
make eval-tool-contracts
make eval-scripted-trajectories
make eval-scripted-answers
make eval-scripted-tool-loops
```

Together these are equivalent to `make eval-ai-offline`: they validate eval
schemas and errors and execute the public PolicyEngine tool contract, including
data-backed cases and exact and tolerance-based 2026 household and reform
goldens. They complement pytest rather than replacing backend or frontend
coverage.

Every pull request also runs the complete live suite:

```bash
ANTHROPIC_API_KEY=... make eval-ai-live
```

Every live model case runs three independent trials with production model
routing. The report exposes pass@1 and pass^3, and any failed trial fails the
job. Data-backed deterministic tool-contract and live tool-loop cases require
`HUGGING_FACE_TOKEN` and `RUN_DATA_EVALS=1`; CI supplies both on every pull
request. There is no path-based, nightly-only, or manually triggered coverage
tier.

`make test-backend` writes branch-aware Python coverage to `coverage.xml` and
prints missing lines. Its coverage boundary includes all repository Python:
`backend/`, `.github/scripts/`, and `modal_app.py`, excluding
`backend/tests/`. The command fails when total backend coverage is below 80%,
and the backend Codecov project status enforces the same minimum.

`make test-frontend` writes frontend coverage to
`frontend/coverage/lcov.info` before running the production build. Vitest
includes all TypeScript and TSX files under `frontend/src/`, including files
that no test imports.

Pull-request and main-branch CI upload these reports to Codecov under separate
`backend` and `frontend` flags. The backend project status enforces 80%; frontend
coverage is reported for visibility and is intentionally non-blocking. Uploads
authenticate with short-lived GitHub OIDC tokens. A rejected backend upload fails
CI; the frontend upload runs after the production build and remains non-blocking.
Repository branch protection must explicitly require the backend Codecov status
after its first upload creates that check.

For an authenticated end-to-end check of the Enhanced FRS society lifecycle
and every official derivative adapter, run:

```bash
HUGGING_FACE_TOKEN=... RUN_DATA_EVALS=1 PYTHONPATH=backend \
  python -m pytest backend/tests/test_data_integration.py
```

The default local pytest suite skips this credentialed test. PR CI runs it in a
dedicated `Configured Enhanced FRS integration` job using the repository
secret. It checks the configured default dataset identity, the full derivative
lifecycle, and broad 2026 fiscal and distributional ranges.

If a command cannot run locally because dependencies or credentials are missing,
state that explicitly in the handoff.
