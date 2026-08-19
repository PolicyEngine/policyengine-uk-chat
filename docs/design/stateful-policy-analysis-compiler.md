# Stateful Policy Analysis Compiler

UK Chat processes every user message through the same typed sequence. A model
may propose what the user means, but server code validates the proposal,
maintains semantic request history, resolves authoritative inputs, compiles
calculation instructions, controls lifecycle changes, and validates actual
operation results.

## Directional architecture

```mermaid
flowchart TB
    classDef model fill:#fde2b8,stroke:#b26a00,color:#222
    classDef deterministic fill:#dff3df,stroke:#2e7d32,color:#222
    classDef persisted fill:#e8ddff,stroke:#7455b8,color:#222
    classDef execution fill:#dcecff,stroke:#2867a8,color:#222

    Input["ChatTurnInput<br/>session and stable turn identifiers"]
    Input --> Recover["Recover expired attempts<br/>for this session"]:::deterministic
    Recover --> Load["Load AnalysisSessionState<br/>and immutable related records"]:::persisted
    Load --> Receipt{"Existing turn receipt?"}
    Receipt -->|yes| Replay["Replay original category<br/>or report still processing"]:::deterministic
    Receipt -->|no| Interpret["Interpreter<br/>CandidateTurnUpdate"]:::model

    Interpret --> Validate["CandidateValidator<br/>types, evidence, references"]:::deterministic
    Fields["Semantic field registry"]:::deterministic --> Validate
    Validate --> Update["ValidatedTurnUpdate"]:::deterministic
    Update --> Semantic["SemanticRequestReducer"]:::deterministic
    Semantic --> Revision["SemanticRequestRevision<br/>immutable user meaning"]:::persisted

    Revision --> Bind["RequestBinder<br/>defaults, catalogue identifiers,<br/>reform values, output producers"]:::deterministic
    Capabilities["CapabilityRegistry<br/>outputs, producers, templates,<br/>exploratory profiles"]:::deterministic --> Bind
    Catalogue["PolicyEngine catalogue and<br/>authoritative current values"]:::execution --> Bind
    Selector["Optional bounded reform-target selector<br/>identifiers and evidence only"]:::model --> Bind
    Bind --> Decision{"BindingDecision"}
    Decision -->|clarification / unsupported / failed| LifecycleEvent["LifecycleEvent"]:::deterministic
    Decision -->|ready| Bound["BoundRequest<br/>immutable compiler input"]:::persisted

    Bound --> Compile["ExecutionPlanCompiler<br/>pure deterministic compilation"]:::deterministic
    Capabilities --> Compile
    Compile --> Plan["ExecutionPlan<br/>exact graph or restricted profile"]:::persisted
    Plan --> LifecycleEvent

    LifecycleEvent --> Lifecycle["LifecycleReducer<br/>complete WorkflowTransition"]:::deterministic
    Lifecycle --> Commit["commit_transition<br/>version and identity checks,<br/>one database transaction"]:::persisted
    Commit --> State{"Committed phase"}
    State -->|response without calculation| Finalize["finalize_turn"]:::deterministic
    State -->|ready calculation| Claim["Atomic claim creates<br/>ExecutionAttempt and token"]:::persisted
    State -->|replacement pending| Wait["Accepting request waits for prior<br/>attempt to close and plan promotion"]:::deterministic
    Wait -->|poll committed state and recover expired attempt| State

    Claim --> Mode{"Compiled execution mode"}
    Mode -->|standard| Standard["Exact compiled steps"]:::deterministic
    Mode -->|exploratory| Explore["Model selects only compiled<br/>operations and dependencies"]:::model
    Operations["OperationRegistry<br/>input/output adapters, result type,<br/>fact and public-summary extractors"]:::deterministic --> Standard
    Operations --> Explore
    Standard --> Arguments["Separate internal dispatch arguments<br/>from public source-step descriptions"]:::deterministic
    Explore --> Arguments
    Arguments --> OperationEvents["Public operation events<br/>no request-local result identifiers"]:::deterministic
    Arguments --> Dispatch["Validate and dispatch<br/>registered operation"]:::execution
    Operations --> Dispatch
    Watch["Attempt validation<br/>token, lease, cancellation"]:::persisted --> Dispatch
    Dispatch --> Results["Request-local ResultEnvelope values"]:::execution
    Results --> Facts["FactRegister and sanitized summaries"]:::deterministic
    Facts --> Narrate["Narrator<br/>fact references, no operations"]:::model
    Narrate --> Outcome["TurnOutcome"]:::deterministic
    Outcome --> LifecycleEvent
    Lifecycle --> Finalize
    Finalize --> Public["Public streaming events"]
    Public --> Artifact["Live-only chart artifact<br/>removed before conversation save"]:::execution
    Finalize --> Usage["Per-call model usage and<br/>idempotent billing intent"]:::persisted
    Usage --> Retry["Later authenticated request retries<br/>pending intent with immutable charge inputs"]:::deterministic
```

Each arrow is an ownership boundary. A later component may reject its input,
but it does not modify a record owned by an earlier component.

## Distinct state and instruction types

`SemanticRequestRevision` records normalized user meaning, cited evidence,
relationship to an earlier request, inherited or cleared fields, requested
outputs, and invalidation metadata. It excludes server defaults, catalogue
identifiers, runtime versions, and calculation operations. A model can author a
reform intent and typed instruction, but not the normalized `reform` value that
the binder derives from authoritative catalogue data.

`BoundRequest` links to one semantic revision and records compiler-ready values:
server defaults with provenance, authoritative catalogue identifiers, validated
reform values, resolved output producers, and runtime versions. Binding creates
a new immutable record and never overwrites the semantic revision.

`ExecutionPlan` is a compiler-owned instruction document. A standard plan
contains exact operations, arguments, result types, and dependency edges. An
exploratory plan contains the smallest server-defined operation profile that
can produce the requested outputs, plus fixed arguments and resource limits.

`AnalysisSessionState` contains only lifecycle phase, ordering version, and
active or pending record identifiers. It does not contain operation graphs or
calculation values. `ExecutionAttempt` separately records which worker may run
one plan, using a stored token hash and a time-limited lease.

## Reducer and compiler responsibilities

| Component | Input | Output | Exclusive responsibility |
| --- | --- | --- | --- |
| `SemanticRequestReducer` | `ValidatedTurnUpdate`, current semantic records | `SemanticRequestRevision` | Start, revise, inherit, clear, relate, and apply a validated clarification answer |
| `RequestBinder` | semantic revision, capability registry, authoritative catalogue | `Ready`, `NeedsClarification`, `Unsupported`, or `BindingFailed` | Apply defaults, resolve identifiers, calculate typed reforms, and prove every output has complete inputs |
| `ExecutionPlanCompiler` | `BoundRequest`, capability registry | `ExecutionPlan` | Produce deterministic operation instructions and authority limits |
| `LifecycleReducer` | `AnalysisSessionState`, typed lifecycle event | `WorkflowTransition` | Construct the complete next session state and all related record/status changes |
| `AnalysisStateStore` | `WorkflowTransition` | committed state or typed conflict | Check versions and parent identities and commit the transition atomically |

The term “workflow” refers to the overall processing sequence. In code, use the
specific `AnalysisSessionState` or `WorkflowTransition` type instead of treating
the workflow as another mutable planning object.

## Lifecycle behavior

```mermaid
stateDiagram-v2
    [*] --> Idle
    Idle --> AwaitingClarification: accepted request needs information
    Idle --> Ready: bound request and plan committed
    AwaitingClarification --> AwaitingClarification: incomplete answer or next missing field
    AwaitingClarification --> Ready: accepted answer produces a plan
    AwaitingClarification --> Failed: answer is unsupported or binding fails
    AwaitingClarification --> Cancelled: cancellation accepted
    Ready --> Executing: atomic claim creates an attempt
    Ready --> Ready: newer ready plan supersedes unclaimed plan
    Ready --> Cancelled: cancellation accepted
    Executing --> Executing: non-conflicting question advances conversation version
    Executing --> Executing: revision stores pending plan and requests cancellation
    Executing --> Completed: validated results and narration succeed
    Executing --> Failed: execution or narration fails
    Executing --> Cancelled: cancellation without replacement
    Executing --> Ready: active attempt closes and pending plan is promoted
    Completed --> Ready: later complete calculation request
    Completed --> AwaitingClarification: later incomplete calculation request
    Failed --> Ready: corrected or retried request
    Failed --> AwaitingClarification: incomplete correction
    Cancelled --> Ready: complete new request
    Cancelled --> AwaitingClarification: incomplete new request
```

Conversation `state_version` orders accepted state changes. It is not execution
authority. The raw execution token authorizes one durable attempt and remains
valid across unrelated conversation-version changes. A revision during
execution stores at most one pending plan; that plan cannot be claimed until
the active attempt has reached a final status. The request that accepted that
replacement remains open, waits for promotion, and then claims and executes the
replacement; recording a pending plan is not itself a successful response.

## Result and narration boundary

Every operation return value is validated by its registered Pydantic adapter.
Only then can it create a `ResultEnvelope`, satisfy a required result type, or
feed a registered fact/public-summary extractor. Dependency resolution accepts
only completed results from an explicitly permitted prior step in the same
execution.

Complete calculation values and request-local result identifiers live in the
in-memory `TurnResultStore`. They are released after the request and never
enter analysis state, plans, attempts, receipts, usage rows, billing rows,
traces, diagnostics, or conversation records. Narration receives sanitized
summaries and a `FactRegister`; it has no calculation operation definitions.

Internal dispatch arguments and public operation-event arguments are built
separately. Dependency resolution may add a request-local identifier only to
the internal dispatch object; the public event describes the producing step.
A chart is sent as a typed artifact only on the live completed event. Before
conversation persistence or title generation, the frontend replaces its chart
block with a fixed explanatory placeholder.

## Persistence and finalization

`LifecycleReducer` constructs every next `AnalysisSessionState`.
`AnalysisStateStore.commit_transition` then validates state version, session
identity, parent-child links, active-attempt uniqueness, and every conditional
status update. All appended records, status updates, session state, receipt,
usage, and billing-intent writes roll back together on a mismatch.

Narration completes before a successful attempt and receipt are finalized.
`finalize_turn` checks that the typed outcome agrees with the next lifecycle
phase, stores sanitized category-aware replay metadata, and derives public
events. Duplicate processing returns a still-processing response without model
or calculation calls. Finalized duplicates preserve the original category and
do not submit billing again. A processing receipt older than the request timeout
and a reused turn identifier with different content produce typed conflicts
before model work. Recovery compares the exact lease it observed, so a racing
worker heartbeat prevents an incorrect expiry. Billing intents store the user
and immutable charge inputs required for a later idempotent retry.

Operational values and extension procedures are maintained in
`docs/engineering/skills/uk-chat-runtime.md`.
