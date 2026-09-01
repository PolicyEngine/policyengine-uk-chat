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

## Commands

Install backend test dependencies separately from runtime dependencies:

```bash
python -m pip install -r backend/requirements.txt \
  -r backend/requirements-test.txt
```

Before handing off backend changes, run the focused backend tests that cover the
changed code. For broader verification, use:

```bash
make typecheck-backend
make test-backend
```

`make typecheck-backend` runs strict mypy checks over the capability contracts,
artifact and compatibility models, registries, composition, execution context,
executor, application composition, trace models, model adapter, narration
verifier, and chat connector types listed in `backend/mypy.ini`. Keep that
explicit list current when a new typed composition boundary is added.

Before handing off frontend changes, run:

```bash
make test-frontend
```

For changes spanning both sides, run:

```bash
make test
```

Capability-runtime tests are grouped by boundary under `backend/tests/`:
`test_capability_composition.py`, `test_capability_artifacts.py`,
`test_capability_persistence.py`, `test_typed_tools.py`,
`test_capability_chat_service.py`, `test_policy_capabilities.py`,
`test_household_capability.py`, `test_society_capabilities.py`,
`test_invocation_observability.py`, and `test_capability_public_api.py`.
Typed conversational fact coverage lives in `test_conversation_context.py` and
includes definition registration, deterministic reduction, stable identity,
generic multi-fact pending-answer resolution, explicit absence, repository
concurrency, household projection, cross-turn retention, per-turn context
proposals, complete-proposal validation, rejection without partial entity or
fact persistence, one-write optimistic application, and fact-decision debug
output.
Context validation tests must cover the independent semantic reviewer as well
as deterministic checks. Require exactly one verdict per opaque claim
identifier; reject missing, duplicate, and unknown identifiers; ensure the
reviewer prompt contains no retained fact values; accept hypothetical values as
active-scenario facts; allow an unresolved multi-entity relationship; and reject
a direct single-person assignment when the current message supplied only a
multi-person total. A validation result must include every independently
detectable issue rather than stopping after a response, entity, or fact error.
Provider-format retries and the one proposal-repair opportunity are distinct,
and repair tests must assert that the exact rejected proposal and complete issue
set are supplied to the configured complex model.
Catalogue-backed value resolution lives in `test_fact_resolution.py`.
It covers model selection of an exact authoritative catalogue result,
deterministic verification of that selection, deterministic time-period
conversion and single-unknown constraint solving, confirmation and rejection,
SQL context round trips, private debug projections, and refusal to create
definitions for ambiguous mappings. It must also prove that deterministic code
does not choose semantic meaning from a normalized label or retained binding,
that the resolver receives the original proposal and structured validation
problems, that every clarification retains its typed monetary constraint, that
explicit same-definition facts clear only an exactly satisfied relationship,
that malformed legacy pending state is repaired only from unambiguous evidence,
and that user-facing prompts contain no engine identifier or model instruction.
Household capability tests separately prove that pending proposals block
defaults and simulation and that confirmed catalogue-backed facts reach both
household validation and calculation inputs.
Household capability regression coverage must exercise the production
context-only composition for multi-turn input retention rather than relying
only on a fake household extractor. The canonical regression is a generic
`tax` request containing £50,000 followed by an age-only answer; it asserts
canonical tax outputs, no housing questions, durable invocation-default
provenance, the exact simulation income, and inclusion of the validated input
amount in narration facts. A separate regression proves that an unaccounted
sterling amount prevents both household validation and simulation.
`test_household_chat_integration.py` additionally runs that two-turn case through
`ChatTurnService`, SQL conversation-context persistence, SQL waiting-invocation
persistence, real proposal, validation, application, and reduction tools, and the real
household capability composition. It must prove that `PendingQuestion` references
the actual waiting invocation, both records retain identical scope/revision and
requirements, provider tool-use identifiers never enter domain state, the model
schema and model-visible results expose no resumption identifier, a malformed
outcome cannot clear valid pending state, and successful completion removes only
the resumed question and waiting record.
The same module contains a SQL-backed ten-user-turn regression that exercises
the complete `ChatTurnService` boundary with deterministic provider substitutes.
It covers the initial £50,000 tax request, retained age, stable spouse identity,
catalogue-backed `70k` collective-income resolution, an explicit 50/20
allocation, spouse-age correction, a later £80,000 collective constraint,
explicit 50/30 and 45/30 corrections, final recalculation, exact simulation
inputs, and removal of all completed pending state. Keep this test at ten user
turns and run it for both ages 26 and 27. The interpretation substitute MUST run behind the production
`AnthropicContextInterpreter`, omit the current message's normalized `70k` value
from its first provider response, and prove that generic message-to-claim value
preservation causes one bounded repair before fact resolution without using an
aggregate-phrase vocabulary. Provider-contract tests separately cover invalid
relationship cardinality. The interpreter
schema must expose a single ordered claim collection for both direct and
relational assertions, with no model-authored context operations and no separate
unresolved-claim collection. The first explicit split uses the live wording
`50k for me, 20k for them`; the model-backed catalogue selector must choose the
exact returned employment-income variable before deterministic validation and
arithmetic can proceed. Shorter isolated resolver tests do not replace
its cross-turn persistence coverage. Provider-contract tests must also prove
that candidate entities and claims are the only model-authored fact-update route,
that validation-generated operations are not accepted in the provider schema,
that direct and relational claims can coexist in one proposal, and that debug
traces show proposal, validation, optional resolution, validation again, and
application as separate structured calls. They additionally cover
currency prefixes and suffixes, compact suffixes, comma, period, space, and
non-breaking-space grouping, scale words, and English number-word forms.
The SQL-backed regression set also includes the exact confirmation path
`£50,000 request → age 27 → spouse and £70,000 collective income → Yes → spouse
age 29`. It must assert that the initial £50,000 is already an annual accepted
context fact before age clarification, that confirming the calculated £20,000
spouse assignment preserves both amounts, and that simulation receives both
values. Run the same multi-turn boundary with `70k`, `70,000`, `70.000`, and
`70 thousand`; include `Yes, annual` as a confirmation response and assert that
authoritative variable metadata prevents a repeated period question. Across
these paths, tax-only clarification must not request rent or Council Tax,
stable ages must persist, natural person references must replace internal
numbered labels, and provider instruction syntax must not enter assistant text.
Two additional SQL-backed ten-user-turn regressions cover responses to a
retained clarification. One supplies only an annual period; the other supplies
a corrected total and annual period. Both must prove that the current proposal
contains only newly cited field updates, that the server retains the complete
source claim and its original provenance, that authoritative resolution runs
again, that the calculated per-person assignment still requires confirmation,
and that the household calculation completes with the final accepted facts.
Keep each pathway at exactly ten user turns and run all three pathways whenever
the context proposal, validation, resolution, application, or household resume
behavior changes.
Service tests must also prove that an invalid current-message context proposal
causes calculation capabilities to be omitted for that request and that a model
cannot execute a capability identifier that was not offered. This prevents a
rejected update from producing a calculation against the prior context revision.
Each retained deterministic operation must declare a distinct output model.
Contract tests must reject a successful result shaped for a different operation
and reject unknown success fields, while permitting the operation's documented
error form. Cancellation tests must cancel an active `asyncio.Task`, await its
cleanup, and assert that every started invocation has a final `cancelled` trace
with a completion timestamp. Extracted coordinators and result builders require
focused unit tests in addition to the integration tests that exercise their
composition.
The disposable PostgreSQL contract test runs only when
`CAPABILITY_TEST_POSTGRES_URL` is set; SQLite coverage remains part of the
default suite. The configured PostgreSQL database must contain no valuable data:
the Alembic lifecycle test downgrades SQLModel-owned tables to the empty base,
upgrades to head, checks for schema drift, downgrades to revision `0001`, and
re-upgrades. It also proves that an unmanaged `analysis_*` sentinel remains
unchanged. Read `docs/engineering/skills/database-migrations.md` before changing
this test or any migration.

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

For an authenticated end-to-end check that materializes the managed Enhanced
FRS data, runs a real baseline/reform population calculation, and exercises
every official derivative adapter, run:

```bash
HUGGING_FACE_TOKEN=... make test-society-live
```

This test is deliberately excluded from the default suite because it downloads
managed data and runs a full baseline/reform society simulation.

The default backend suite separately runs four SQL-backed ten-user-turn chat
paths that use deterministic provider substitutes. They cover repeated
population calculations, optional population outputs, a transition from a
household calculation to population analysis, and the exact Basic Rate wording
that previously exposed inconsistent reform-target serialization. The fourth
path passes a deterministic Anthropic response through the production reform
resolver and asserts that its tool schema restricts `meaning.parameter_path` to
the returned catalogue paths. It also passes `societal_impact` through output
selection on all ten turns and asserts that the phrase selects only the default
profile without producing a requested-output issue. Every population turn must execute the three
default derivative operations. The first three paths respectively add poverty,
inequality, and programme statistics so their combined assertions cover every
`compute_*` population derivative. They must also assert that population turns
do not invoke numerical narration verification.

If a command cannot run locally because dependencies or credentials are missing,
state that explicitly in the handoff.
