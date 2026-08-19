# Stateful Analysis Compiler Resolution Record

This record maps the ten reviewed problem groups to the implementation that
resolves them. It also records compatibility boundaries, acceptance evidence,
and the verification required before the change is archived.

## Current implementation status

The R1-R10 corrections and the request-compilation, execution, finalization,
projection, billing, and store-interface facades are implemented. The broader
application-service simplification is not complete. The OpenSpec checklist
currently records 212 of 231 tasks complete.

Implemented simplification work includes complete lifecycle-state validation,
typed status mutations, scoped connector protocols, one operation catalogue,
the typed `TurnInterpreter` dependency, `RequestCompiler`, `ExecutionEngine`,
typed `FinalizationResult`, `ChatEventProjector`, billing-intent processing, and
the `AnalysisStore` protocol with `SqlAnalysisStore`.

The remaining work is explicit rather than implicit:

- enforce the final one-way chat-to-analysis import direction;
- separate internal SQL row/version-one parsing concerns and move lifecycle
  choices out of mutating store methods;
- replace `run_analysis_turn` with `AnalysisTurnService` plus a small chat-side
  adapter;
- expand service/store/state-sequence and manual model evaluation coverage;
- remove compatibility aliases and direct internal entry points; and
- run the final PostgreSQL, offline evaluation, strict typing, documentation,
  obsolete-symbol, and complete-diff checks.

Until those tasks are complete, this document is a progress record and must not
be used as evidence that the OpenSpec change is ready to archive.

## Production symbol replacement inventory

| Earlier production concept or symbol | Target symbol or boundary | Compatibility treatment |
| --- | --- | --- |
| `GatewayVerdict`, `GateResult`, and branch-specific routing decisions | `ValidatedTurnUpdate`, typed `BindingDecision`, and `TurnOutcome` | Earlier gateway modules are removed from the active runtime |
| `SlotFact`, `GatingReason`, and duplicated prompt/tool vocabularies | `SemanticFieldSpec` and `CapabilityRegistry` | No active write-path adapter |
| `GatewayExecutionPlan` | `BoundRequest` followed by `ExecutionPlan` | Version-one plan documents have a read-only compatibility reader |
| `ReformIntent` plus direction/value inference and `ReformAssessment` | discriminated `ReformInstruction` plus authoritative catalogue binding | Earlier inference is removed from production |
| raw `CandidateTurnUpdate` passed toward reduction | `CandidateValidator` producing `ValidatedTurnUpdate` | Raw candidates remain only at the model boundary |
| mutable request object containing user and server values | separate `SemanticRequestRevision` and `BoundRequest` | Version-one revision documents are upgraded on read |
| shared request/lifecycle reducer responsibilities | `SemanticRequestReducer` and `LifecycleReducer` | No ambiguous aliases on the active path |
| plan claim fields used as worker authority | durable `ExecutionAttempt` and hashed execution token | Version-one execution metadata is read-only |
| branch-specific receipt updates and aggregate usage | `finalize_turn`, `ModelUsageEntry`, and `BillingIntent` | Version-one receipts are upgraded on read |
| workflow snapshot construction in coordinator/persistence branches | reducer-owned `AnalysisSessionState` and `WorkflowTransition` | Version-one session documents are upgraded on read |
| direct coordinator calls across semantic reduction, binding, and plan compilation | `RequestCompiler` | The facade is active; internal compatibility helpers remain for tests and migration |
| direct standard/exploratory strategy selection | `ExecutionEngine` | The facade is active; direct strategy functions remain temporary internal compatibility surfaces |
| analysis finalization constructing chat events and prices | `FinalizationResult`, `ChatEventProjector`, and billing adapters | Production finalization is separated; the coordinator still invokes projection until `AnalysisTurnService` lands |
| `AnalysisStateStore` as the concrete application name | `AnalysisStore` protocol and `SqlAnalysisStore` implementation | `AnalysisStateStore` remains a temporary alias |
| `run_analysis_turn` as the complete application coordinator | `AnalysisTurnService.run(TurnCommand)` | Target service is not implemented; the current coordinator is the documented temporary exception to one-way imports |

The public chat event fields remain compatible. New internal outcome categories
are projected through the existing streaming protocol, with explicit conflict,
failure, cancellation, duplicate, and still-processing events where supported.

## R1–R10 implementation mapping

| Review item | Final implementation | Primary evidence |
| --- | --- | --- |
| R1 — untrusted candidates and mixed ownership | `candidate_validation.py`, `SemanticFieldSpec`, `ValidatedTurnUpdate`, immutable semantic revisions and bound requests; normalized `reform` is binder-only | candidate, interpreter, reducer, model, and property tests; direct normalized-reform rejection; turn-interpretation eval cases |
| R2 — several components constructing lifecycle state | exhaustive `LifecycleEvent` family and `LifecycleReducer`, including immutable clarification-resolution construction | lifecycle transition table/property tests, clarification outcome tests, and static coordinator inspection |
| R3 — incomplete readiness and duplicated capability knowledge | versioned `CapabilityRegistry`, typed `OutputProducer`, four-way `BindingDecision`, pure `ExecutionPlanCompiler` | capability consistency, binder decision, compiler graph, and multi-output tests |
| R4 — qualitative reform direction becoming a value | discriminated `ReformInstruction`, authoritative current/inactive values, bounded target selector | strict boolean acceptance/rejection, zero, amount, percentage, abolition, ambiguity, and selector tests/evals |
| R5 — model-created calculation authority | exact standard graphs and restricted exploratory profiles owned by the registry/compiler | compiler determinism and exploratory authorization tests |
| R6 — partial persistence updates | `WorkflowTransition` and atomic `commit_transition` with version, affected-row, session, and parent validation | SQLite rollback tests, migration checks, and PostgreSQL interleavings |
| R7 — plan claim used as execution identity | durable `ExecutionAttempt`, unpredictable token, stored hash, lease, heartbeat, and exact-observed-lease recovery | claim, token, heartbeat/recovery race, expiry, request-driven recovery, and two-worker tests |
| R8 — conversation version coupled to execution and replacement | independent token validation plus cancel-and-wait behavior in which the accepting request executes the promoted plan | unrelated-version, replacement ownership, cancellation, late-completion, and promotion/claim tests |
| R9 — operation-name success and reusable result references | complete-schema input adapters, allowlisted output adapters, execution-local `ResultEnvelope`, explicit dependencies, separate public argument projection, registered fact/public-summary extractors, and live-only chart artifacts | input boundary, explicit household result shapes, unknown output field, malformed output, result type, dependency, public identifier exclusion, chart delivery/persistence exclusion, required-result, and end-to-end durable-data tests |
| R10 — divergent narration, replay, usage, billing, conflict, and public-event branches | discriminated `TurnOutcome`, one `finalize_turn`, explicit commit-versus-receipt-only intent, category-aware replay, complete failure events, stale-processing and content-mismatch conflicts, per-call usage, and retryable immutable billing intents | outcome/phase matrix, common conflict finalization, narration failure, public streaming replay/failure matrix, stale processing, idempotency mismatch, pricing, cache token, and pending-billing retry tests |

Database migration `006_analysis_compiler_hardening.sql` adds bound requests,
durable attempts, active and pending identifiers, per-call usage, billing
intents, replay metadata, indexes, foreign-key relationships, and the
PostgreSQL one-active-attempt uniqueness condition. Migration 005 adds the
stable-turn external billing operation. Both migrations are additive for an
immediate application rollback.

Manual AI evaluation coverage is in
`evals/cases/turn_interpretation/core.yaml`. It includes later-turn revision,
another related simulation, clarification, exact boolean reform values,
ambiguous reform target selection, exploratory restrictions, forbidden
execution-control fields, stale references, and unsupported narration numbers.
Fixtures record candidate structure, binding/plan contracts, permitted
operations, lifecycle outcome, and public outcome category where applicable.

## Acceptance invariants and evidence

1. Raw model candidates cannot reach `SemanticRequestReducer`: reducer input is
   `ValidatedTurnUpdate`; runtime and static tests reject the raw union.
2. Model-authored fields cannot name operations, runtime versions, dependencies,
   limits, or normalized reforms: interpreter schema generation uses only
   candidate-authorable semantic field specs and adversarial tests/evals reject
   those fields.
3. Readiness requires a validated producer for every requested output: binder
   and exhaustive capability tests cover supported and unsupported pairs.
4. Qualitative direction cannot become a number: `DirectionOnlyReform` returns
   clarification; only exact or registered deterministic transformations bind.
5. Boolean values preserve exact type: strict scalar adapters and round-trip,
   candidate-validation, wrong-type rejection, binding, compiler, and eval tests
   cover both values.
6. Only `LifecycleReducer` creates next `AnalysisSessionState` values: source
   inspection tests reject coordinator state construction.
7. Persistence rejects invalid reducer relationships: version, wrong-session,
   wrong-parent, affected-row, and rollback tests exercise the checks.
8. At most one attempt is active per session: portable pre-commit validation and
   database partial uniqueness are exercised by two-worker tests.
9. Conversation version changes do not invalidate tokens: token verification
   tests advance the conversation state independently.
10. A replacement cannot be claimed early or abandoned after promotion:
    pending-plan tests reject claim until the active attempt closes, then prove
    the accepting request claims, executes, and finalizes the promoted plan.
11. Claimed attempts reach a final status or bounded recovery: outcome and expiry
    tests cover completed, failed, cancelled, superseded, and expired records.
12. Required results use validated values: complete input-contract,
    allowlisted output-adapter, explicit household baseline/reform structure, and
    required-result tests reject malformed, undeclared, or merely declared
    success.
13. Result references do not cross executions: standard, exploratory, and
    result-store tests reject foreign identifiers.
14. Narration failure cannot disagree with durable state: coordinator/finalizer
    tests close the plan, session, and receipt for explanations, and additionally
    close the execution attempt for calculations.
15. Replay preserves category and billing idempotence: finalization and public
    streaming matrices plus repeated external submission tests cover every
    durable category without duplicate billing.
16. Every model call uses its actual model for usage and pricing: interpreter,
    selector, exploratory, and narration attempts produce separate entries;
    mixed-model and cache-token tests verify pricing.
17. Only the binder creates normalized reform values; candidates may supply only
    permitted reform intent and typed instructions.
18. A replacement-accepting request remains responsible through promotion,
    claim, execution, and finalization.
19. Public operation events and durable rows contain no request-local result or
    simulation identifiers; internal dependency resolution and public argument
    projection are separate.
20. Recovery cannot close an attempt whose lease changed after the recovery
    read, and stale processing receipts do not remain active indefinitely.
21. Chart data reaches only the live completed event and is excluded from the
    turn receipt, duplicate replay, saved conversation, and title input.
22. Cancellation is checked before every standard and exploratory operation.
23. Failed public outcomes carry complete final-response metadata and both
    frontend streaming paths finalize and save them.
24. Billing intents contain the user and immutable finalized charge inputs
    required for a later idempotent retry.
25. A turn-identifier content mismatch becomes a typed public conflict before
    model work and leaves the original receipt unchanged.

## Operational decisions

- Execution lease: 180 seconds.
- Heartbeat interval: 15 seconds.
- Recovery eligibility: immediately after `lease_expires_at`.
- Hosted request timeout: 600 seconds; individual calculation operations are
  cooperatively cancellable between dispatches.
- Sanitized attempt, receipt, and per-call usage retention: conversation
  lifetime, removed with conversation deletion.
- Complete calculation results and request-local result identifiers: memory
  only for the active request.
- Live chart artifacts: emitted only on the original completed stream and
  replaced with a fixed explanatory placeholder before conversation storage.
- Conflict finalization: the common finalizer persists a conflict receipt through
  a receipt-only transition and leaves the current session snapshot unchanged.
- Duplicate processing receipt timeout: 600 seconds, after which a duplicate
  receives a retryable conflict and must use a new turn identifier.

## Verification checkpoint

Latest checks against commit `bcb1e9e` on 19 August 2026:

- The complete backend suite passed: 538 passed, 7 skipped, with 83.07% coverage
  against the configured 80% requirement. Five PostgreSQL concurrency cases and
  two data-dependent cases accounted for the skips.
- The complete frontend check passed: 73 tests passed and the Next.js production
  build completed successfully.
- The scoped strict check passed for `analysis.store`,
  `analysis.dependencies`, `analysis.lifecycle`, `analysis.request_compiler`,
  and `analysis.execution_engine`.
- Focused request compiler, capability, operation catalogue, semantic reducer,
  lifecycle, and execution-engine tests passed after the final typing cleanup.
- `git diff --check` passed, and the pushed branch matched commit `bcb1e9e`.
- The remaining two backend warnings originate in external Starlette and
  PolicyEngine dependencies.

Earlier verification before the later facade simplification recorded 140
offline evaluation cases passed with 27 skipped and all five synchronized
PostgreSQL interleavings passed against PostgreSQL 16. Those results remain
useful historical evidence, but they are not a substitute for the final rerun
required by OpenSpec tasks 28.4 and 29.5.

Final verification is therefore still outstanding. In particular, the current
branch has not rerun the PostgreSQL cases with
`ANALYSIS_TEST_POSTGRES_URL`, has not rerun the offline evaluation after the
latest facade changes, and has not completed strict checking or structural
import enforcement for the future turn service and compatibility cleanup.

Post-verification corrections additionally established that:

- explicit reform toggles accept only JSON booleans and reject string or numeric
  substitutes at both the model and candidate-validation boundaries;
- household results expose dynamic PolicyEngine variables only inside explicit
  entity containers and reject undeclared top-level fields;
- public streaming tests distinguish processing, completion, conflict, failure,
  and cancellation replays, while a replacement request remains responsible for
  waiting, claiming, executing, and finalizing its promoted plan;
- an execution payload, a fact value, and a request-local result identifier with
  unique sentinels remain absent from every durable analysis row after complete
  coordinator finalization;
- public operation events describe dependency source steps without exposing
  internal result identifiers, and live chart artifacts are removed before
  conversation persistence;
- expired-attempt recovery compares the exact observed lease, and abandoned
  processing receipts close through a retryable conflict; and
- billing retries use persisted user and immutable finalized charge inputs.
