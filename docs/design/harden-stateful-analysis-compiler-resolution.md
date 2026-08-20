# Stateful Analysis Compiler Resolution Record

This record maps the ten reviewed problem groups to the implementation that
resolves them. It also records compatibility boundaries, acceptance evidence,
and the verification required before the change is archived.

## Current implementation status

The R1-R10 corrections and the five-role application structure are implemented.
Normal callers use `AnalysisTurnService`, `TurnInterpreter`, `RequestCompiler`,
`ExecutionEngine`, and `AnalysisStore`; chat projection and billing processing
remain adapters outside the analysis package.

The implementation includes complete lifecycle-state validation, typed status
mutations, scoped connector protocols, one operation catalogue,
`AnalysisTurnService.run(TurnCommand)`, typed `FinalizationResult`, chat-side
projection, billing-intent processing, and typed mutation commands on
`SqlAnalysisStore`.

Implementation and verification are complete. The OpenSpec change is ready for
review and archival; archival remains a separate explicit workflow action.

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
| direct coordination across semantic reduction, binding, and plan compilation | `RequestCompiler` | Public binder/compiler compatibility functions are removed |
| direct standard/exploratory strategy selection | `ExecutionEngine` | Strategy functions are private and separately tested |
| analysis finalization constructing chat events and prices | `FinalizationResult`, `ChatEventProjector`, and billing adapters | The chat adapter alone projects service results |
| `AnalysisStateStore` as the concrete application name | `AnalysisStore` protocol and `SqlAnalysisStore` implementation | The temporary alias is removed |
| `run_analysis_turn` as the complete application coordinator | `AnalysisTurnService.run(TurnCommand)` | `run_analysis_turn` is now a chat-side compatibility stream over typed service results |

The public chat event fields remain compatible. New internal outcome categories
are projected through the existing streaming protocol, with explicit conflict,
failure, cancellation, duplicate, and still-processing events where supported.

## R1–R10 implementation mapping

| Review item | Final implementation | Primary evidence |
| --- | --- | --- |
| R1 — untrusted candidates and mixed ownership | `candidate_validation.py`, `SemanticFieldSpec`, `ValidatedTurnUpdate`, immutable semantic revisions and bound requests; normalized `reform` is binder-only | candidate, interpreter, reducer, model, and property tests; direct normalized-reform rejection; turn-interpretation eval cases |
| R2 — several components constructing lifecycle state | exhaustive `LifecycleEvent` family and `LifecycleReducer`, including immutable clarification-resolution construction | lifecycle transition table/property tests, clarification outcome tests, and static turn-service inspection |
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
   inspection tests reject turn-service state construction.
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
14. Narration failure cannot disagree with durable state: turn-service/finalizer
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

Latest checks against the working tree based on commit `1ae0bb1` on 20 August
2026:

- The complete backend suite passed: 554 passed, 8 skipped, with 83.59% coverage
  against the configured 80% requirement. Six PostgreSQL-only cases and two
  data-dependent cases account for the declared skips in the default run.
- The dedicated PostgreSQL 16 run passed all 6 cases: the common typed store
  contract plus claim-versus-claim, claim-versus-cancel,
  revision-versus-completion, recovery-versus-completion, and pending-plan
  promotion interleavings. The setup applied migration 006 twice and then ran
  the same model-owned compatibility-table bootstrap used by production.
- The complete frontend check passed: 73 tests passed and the Next.js production
  build completed successfully.
- The offline evaluation passed 146 cases, failed none, and skipped 27 according
  to their declared requirements. The generated PolicyEngine tool-contract
  fixture was current.
- The scoped strict check passed for `analysis.store`,
  `analysis.dependencies`, `analysis.lifecycle`, `analysis.request_compiler`,
  `analysis.execution_engine`, `analysis.turn_service`, and
  `chat.analysis_adapter`.
- Strict OpenSpec validation passed for
  `harden-stateful-analysis-compiler`.
- Structural import and obsolete-symbol checks found no analysis-to-chat,
  analysis-to-evaluation, or removed production API references. The complete
  changed-path review and `git diff --check` passed.
- The two backend warnings originate in external Starlette and PolicyEngine
  dependencies.

Post-verification corrections additionally established that:

- Anthropic tool schemas that contain discriminated unions are sent without
  provider strict mode, then decoded and validated locally against the same
  typed candidate and narration models; real provider calls complete for both
  interpretation and narration;
- household calculation results represent each `person` entity as a list of
  typed records, matching the PolicyEngine response contract; and
- a live two-turn request through the frontend proxy completed both household
  operations, then inherited the original person and analysis year while
  revising only employment income on the second turn;
- canonical analysis-kind classification accepts ordinary user wording without
  requiring internal category labels, while still requiring a closed enum value
  and exact current-message evidence;
- catalogue binding selects one strictly highest-confidence authoritative match
  and retains clarification when the best authoritative candidates tie;
- the same unique-best rule applies to reform targets, so an exact ordinary
  policy name is not made ambiguous by lower-confidence phrase matches;
- PolicyEngine variable discovery passes its result limit separately from the
  optional entity filter, so ordinary variable queries return catalogue
  candidates instead of being filtered by a numeric limit value;
- registered analysis kinds and key semantic fields provide model-facing
  interpretation guidance, including ordinary aggregate phrases such as “how
  many,” “people,” “households,” “total,” and “average,” while unstated UK and
  year values remain server defaults;
- six focused live interpretation-and-compilation cases pass for ordinary
  policy-value, proposed-change, reform-cost, programme-caseload, family-
  entitlement, and conceptual-explanation wording without internal category
  labels;
- the exact UI starter prompt `What's the personal allowance?` completes through
  the frontend proxy, resolves the intended parameter, and narrates only the
  labelled current value as `£12,570`;
- explicit reform toggles accept only JSON booleans and reject string or numeric
  substitutes at both the model and candidate-validation boundaries;
- household results expose dynamic PolicyEngine variables only inside explicit
  entity containers and reject undeclared top-level fields;
- public streaming tests distinguish processing, completion, conflict, failure,
  and cancellation replays, while a replacement request remains responsible for
  waiting, claiming, executing, and finalizing its promoted plan;
- an execution payload, a fact value, and a request-local result identifier with
  unique sentinels remain absent from every durable analysis row after complete
  turn-service finalization;
- public operation events describe dependency source steps without exposing
  internal result identifiers, and live chart artifacts are removed before
  conversation persistence;
- expired-attempt recovery compares the exact observed lease, and abandoned
  processing receipts close through a retryable conflict; and
- billing retries use persisted user and immutable finalized charge inputs.
