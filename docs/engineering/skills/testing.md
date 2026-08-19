# Testing

Use this skill whenever adding, moving, or reviewing tests.

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
- Stateful analysis reducer tests use Hypothesis for generated valid and
  adversarial update sequences, including starts, revisions, clarification
  answers, execution questions, cancellations, stale identifiers, foreign
  execution references, and invalid evidence. Keep examples deterministic
  under pytest's recorded seed output and isolate database concurrency tests
  from shared developer data.

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

Changes to the typed analysis connectors, lifecycle core, request compiler, or
execution engine also require the currently scoped strict check:

```bash
make typecheck-backend
```

This target checks `analysis.store`, `analysis.dependencies`,
`analysis.lifecycle`, `analysis.request_compiler`, and
`analysis.execution_engine`. It is intentionally narrower than the complete
backend while older imported modules are migrated. Expand the target when a new
public analysis connector, including `AnalysisTurnService`, is added; do not
claim that the complete analysis package is strictly checked yet.

Before handing off frontend changes, run:

```bash
make test-frontend
```

For changes spanning both sides, run:

```bash
make test
```

Stateful analysis persistence changes also require PostgreSQL 16 interleaving
tests. Point the test process at an empty or disposable database owned by the
developer, then run:

```bash
ANALYSIS_TEST_POSTGRES_URL=postgresql://postgres:postgres@localhost:5432/analysis_test \
  make test-analysis-postgres
```

The test target applies migration 006 twice to verify repeatability and uses
explicit thread synchronization for claim-versus-claim, claim-versus-cancel,
revision-versus-completion, recovery-versus-completion, and pending-plan
promotion. The pull-request backend job runs the same file against its
dedicated PostgreSQL 16 service. Never point this target at production.

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

This test is deliberately excluded from the default suite because it downloads
managed data and runs a full baseline/reform society simulation.

If a command cannot run locally because dependencies or credentials are missing,
state that explicitly in the handoff.
