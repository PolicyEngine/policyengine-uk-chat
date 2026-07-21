# Testing

Use this skill whenever adding, moving, or reviewing tests.

## Layout

- Backend tests live under `backend/tests/`.
- Frontend checks currently run through the frontend build.
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

## Commands

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
