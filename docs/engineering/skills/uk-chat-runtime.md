# UK Chat Runtime

Use this guide when changing the chat model pathway, prompts, capability or tool
definitions, calculation behavior, retained conversational state, or the
boundary between model decisions and deterministic code.

## Active runtime

`POST /chat/message` has one implementation: the capability-oriented runtime
described below. There is no request-time or deployment-time selector for an
alternate chat implementation. Rollback means deploying the preceding known-good
application version; the current application does not contain an inactive copy
of the former runtime.

## Runtime structure

The model receives the complete supported conversation history. It may answer
directly or invoke one or more public capabilities. A capability is a cohesive
object that coordinates typed tools and declared prerequisite capabilities. A
tool is one scoped operation with validated Pydantic input and output.

The central object relationships are deliberately shallow:

```text
ChatTurnService
  -> ConversationContextRepository
  -> FactDefinitionRegistry
  -> ContextChangeCoordinator
       -> ContextInterpreter (`propose_context_change`)
       -> ContextProposalReviewer + ContextChangeValidator (`validate_context_change`)
       -> ContextChangeResolver (`resolve_context_change`, when validation requires it)
       -> ContextChangeApplier (`apply_context_change`)
  -> PendingQuestionCoordinator
  -> ConversationModel
  -> CapabilityRegistry
  -> InvocationExecutor
       -> Capability[Input, Output]
       -> Tool[Input, Output]
       -> InvocationTracer
  -> CapabilityContext
       -> typed ConversationContext revision
       -> ConversationCapabilityRepository
       -> request-local TurnResultStore
       -> cancellation and model-usage accounting
```

`InvocationExecutor` is the only normal path for registered calls. It validates
caller permission, input and output models, declared dependencies, cancellation,
parent invocation identity, and trace status. Capability implementations do not
select global intent and the executor does not resolve conversational input.
Cancelling a running asynchronous invocation must be awaited. Both explicit
request cancellation and `asyncio.Task.cancel()` finish the invocation trace as
`cancelled`; they must not leave a `running` record or unfinished cleanup.

Core ownership:

- `backend/capabilities/contracts.py` and `backend/tools/contracts.py`: immutable
  specifications, generic interfaces, visibility, caller types, and typed
  outcomes.
- `backend/capabilities/registry.py`, `backend/tools/registry.py`, and
  `backend/capabilities/composition.py`: registration and startup validation.
- `backend/capabilities/executor.py` and `backend/capabilities/context.py`:
  authorized execution and request-scoped dependencies.
- `backend/capabilities/application.py`: concrete startup composition.
- `backend/chat/capability_service.py`: full-history model loop and response
  finalization.
- `backend/chat/context_coordination.py`: one context-change transaction and
  synchronization of capability clarification requirements into typed context.
- `backend/chat/model_port.py`: provider-specific model adapter.
- `backend/conversation_context/`: stable entities, immutable facts, fact
  definitions and registry, context projections, model-assisted interpretation,
  independent per-claim semantic review, deterministic whole-proposal
  validation, model-assisted authoritative resolution, atomic application,
  household views, and the persistence contract.
- `backend/capabilities/artifacts.py`: immutable transferable values.
- `backend/capabilities/household_input.py`: household calculation
  requirements, invocation-local defaults, typed assumption input, and the
  deterministic `HouseholdInputResolver`.
- `HouseholdEvidenceCoordinator`, `HouseholdInvocationCoordinator`, and
  `HouseholdResultPresenter` in `backend/capabilities/household.py`: household
  evidence merging, cross-turn invocation resumption, and result construction,
  respectively; the tool and capability coordinate these objects.
- `backend/persistence/`: SQL context, artifact, waiting-input, trace,
  deletion, and idempotency repositories.
- `backend/tools/typed_dispatch.py`: typed objects around the retained 21
  deterministic tool functions. Every function has its own success-result model;
  a generic JSON result is not a valid substitute for an operation contract.
- `backend/engine/`: PolicyEngine calculation and catalogue implementations.

Keep prompt and provider behavior at the chat or capability edge. Keep policy
calculation, validation, aggregate derivation, and chart construction behind
typed tools.

## Required capability use

Capability use is optional for ordinary supported conversation, except for
these authoritative mappings:

- Government policy formulation, scope, formula, or calculation-method
  questions require `policy_information`.
- Benefit amounts or policy impacts for a described or retained household
  require `household_analysis`, or `analysis_follow_up` over a compatible
  household result.
- Population-wide reform or benefit impacts require `society_analysis`, or
  `analysis_follow_up` over a compatible population result.
- A request spanning these classes requires every applicable capability.

These rules appear in `MANDATORY_CAPABILITY_CONTRACT`, public capability
descriptions, integration tests, and capability-aware trajectory evaluations.
Do not answer a required class from model memory alone.

`ConversationContext` is loaded before each relevance assessment. Relevance
receives a compact read-only projection so a short answer can be assessed
against pending typed requirements. `conversation_relevance` runs once before
each normal turn and returns only
`relevant`, `uncertain`, or `clearly_out_of_scope`. It cannot select another
capability, construct domain input, or modify artifacts. Only clear positive
evidence of an unsupported jurisdiction or unrelated request should produce a
scope refusal.

After a relevant or uncertain result, context processing follows four explicit
responsibilities. First, the private `ContextInterpreter`, registered as
`propose_context_change`, receives the exact current message, conversation
excerpts for reference resolution, registered fact definitions, stable entities,
active facts, and pending requirements. It returns one `ContextChangeProposal`
containing candidate entities, one ordered discriminated `changes` collection,
and an optional focus change. A new assertion is a `FactClaim`; an action on an
existing server-authored fact-resolution proposal is a
`PendingFactResolutionResponse`. Every supported factual assertion uses the same
collection, whether it is a direct value for one subject, an additive value over
several subjects, or a value that requires period conversion. Its provider schema contains no context operations,
fact-setting operations, resolver-authored proposal, or separate unresolved-claim
collection. The interpreter cannot create a pending resolution, replace
capability-owned pending questions, allocate an amount, select a capability, or
persist context.

Second, the `validate_context_change` operation combines two deliberately
separate checks. `ContextProposalReviewer` independently returns exactly one
typed `SemanticClaimReview` for each opaque claim identifier. It receives only
the exact current message, proposed claims, known entity identities, and active
scenario scope. It does not receive retained fact values, so it checks whether
the proposal represents the message without mistaking a valid correction for a
conflict with prior state. Missing, duplicate, or unknown verdict identifiers
fail validation. It treats a hypothetical value as valid active-scenario input,
may support an unresolved relationship over several people, and rejects a
direct single-person assignment when the message supplied only a multi-person
total.

`ContextChangeValidator` then validates the complete proposal and semantic
reviews against current-message evidence, registered definitions, stable entity
types, value and period contracts, relationship cardinality, correction rules,
conflicts, and the complete context schema. It reports every independently
detectable issue in one result and may generate internal operations for directly
valid claims. It does not choose semantic mappings, calculate allocations,
phrase questions, search, or write context. A failure returns machine-readable
problems and preserves the prior context.

A short answer to a pending clarification does not reconstruct the original
claim. The interpreter emits a `PendingFactResolutionResponse` containing the
pending identifier and only schema-addressed fields supported by the exact new
message. The validator checks those field values and their citations, merges
them into the complete server-owned source claim, and sends the merged claim
through normal authoritative resolution. Retained amounts, subjects,
relationships, and original evidence do not need to appear in the latest
message and cannot be replaced without new supporting evidence.

Third, when authoritative semantic resolution is required,
`ContextChangeResolver`, registered as `resolve_context_change`, receives the
original proposal, the validator's problems, compatible accepted values, and
bounded PolicyEngine catalogue search results. If the interpreter model already
selected a registered definition with an exact returned engine binding, the
resolver validates and uses that proposal selection. Otherwise its model adapter
chooses a semantic mapping and may select only an exact returned variable at
high confidence. Deterministic code then verifies the selected catalogue identity,
entity and value type, converts compatible periods, substitutes accepted facts,
and solves only a supported equation with one remaining unknown. Deterministic
code never selects a variable from label similarity, a retained binding, or a
phrase-specific rule. Ambiguous mappings and equations with more than one
unknown return structured clarification details. The validator checks the
resolved operations again against the original proposal and prior revision.

Fourth, `ContextChangeApplier`, registered as `apply_context_change`, accepts
only a fully validated outcome and performs one optimistic repository write. It
does not interpret, repair, or calculate. No part of one current-message change
is stored before the complete result validates.

The interpreter decides whether the user's language states one direct value or
a relationship among several subjects. Deterministic code does not maintain a
vocabulary of phrases that imply an aggregate. The `FactClaim` schema instead
requires exactly one subject for a direct relationship and at least two subjects
for an additive relationship. A provider-schema failure may receive its own
representation retry. A proposal that passes provider validation but fails
semantic or deterministic validation receives one separate bounded repair
attempt containing the exact rejected proposal and the complete structured
issue set. The repair uses the configured complex model. Provider-format
retries do not consume that repair opportunity.
A deterministic `MonetaryExpressionParser` recognizes currency prefixes and
suffixes, `k` and `m`, comma, period, space, and non-breaking-space grouping,
`thousand`, `grand`, and `million`, and English number words ending in those
scales. Thus `70k`, `70,000`, `70.000`, `70 thousand`, and `seventy thousand`
all compare as `Decimal("70000")`. This normalization validates a claim's
typed value only: it does not select a variable, entity, relationship, period,
capability, or allocation. Additive fact resolution substitutes only accepted
current-scope facts from `ConversationContext`. A calculation artifact cannot
stand in for a user assertion that is absent from context; this prevents an
artifact from masking a lost proposal claim. Deterministic checks still validate
the model-selected catalogue candidate and perform the equation.

A direct monetary claim with an unstated period remains in the same claim list
with a null period. Validation may inherit a period from one compatible active
fact for that subject or one compatible pending fact-resolution constraint.
Otherwise it sends the claim to authoritative resolution rather than omitting
it. When the model-selected exact PolicyEngine variable supplies an
authoritative definition period and the amount needs no arithmetic conversion,
resolution emits the ordinary fact-setting operation at that period. The value
therefore persists in `ConversationContext` before capability selection. If the
authoritative metadata still supplies no period, the claim remains unresolved
so capability input resolution may apply a documented invocation-local default
or ask for the missing period. Relational monetary claims may likewise retain
an unstated period because deterministic resolution can derive a compatible
period from validated facts or return a focused clarification.

A calculated assignment is persisted as a `PendingFactResolution` and is not an
accepted fact until the user confirms it. Confirmation applies the exact stored
assignment through the normal reducer without another model calculation. Only
an `awaiting_confirmation` proposal may be confirmed. Every clarification
proposal retains its complete source `FactClaim`, typed amount, stated period,
relationship, stable entity references, original evidence, and separately cited
field supplements. A rejection requests explicit values;
accepted direct replacement facts clear the disputed proposal only when one
common registered monetary definition covers every referenced entity and the
normalized values exactly satisfy the retained total. User-facing prompts use
ordinary labels and never expose engine identifiers or model instructions.
Calculation capabilities do not validate, default, or simulate while a relevant
proposal is pending.

The validator also enforces claim conservation: every proposed fact claim must
produce a validated internal operation, remain explicitly listed for
authoritative resolution, or produce a structured claim-specific issue. A
validation result cannot report no change or readiness after silently omitting
a claim.

The validator uses `ContextReducer` as a pure primitive to check fact keys,
versions, subjects, value types, scopes, explicit absence, conflicts,
corrections, and expected revisions without persisting the provisional result.
After optional resolution, it combines all validated internal operations against
the original revision and validates the complete `ConversationContext`. A
current message produces either one fully validated new revision through the
applier or no write. A rejected or conflicting operation rejects the whole
current-message change, so an entity or fact cannot be retained while a related
value claim is lost.

Provider-schema, semantic-review, and deterministic validation issues are
represented by machine-readable codes, exact claim identifiers or paths, and
cited evidence. If repair and optional resolution still fail, the prior context
remains unchanged and the runtime offers no calculation capability for that
turn. The conversational model phrases a concise natural clarification from the
structured details; validation code supplies deterministic wording only as an
emergency fallback. Every returned capability call is checked against the exact
identifiers offered in that model request, so a hidden or withheld capability
cannot execute against stale context. Neither context object applies capability
defaults, decides global intent, or calculates a result.

## Public and private operations

Every `ToolSpec` and `CapabilitySpec` must explicitly declare `public` or
`private` visibility. Visibility controls user-facing activity projection; it
does not grant caller permission. Caller permission is a separate closed set.

- Public capabilities are normally available to the conversational model and
  their calls are visible in ordinary activity output.
- Private capabilities and tools support validation, relevance, input
  resolution, finding extraction, and numerical verification. They are visible
  only when an authorized user enables debug mode.

Every call is recorded whether debug mode is enabled or not. Trace storage uses
a fixed operational metadata allowlist plus optional `debug_input` and
`debug_output` JSON created only after the applicable model-facing or registered
typed boundary validates the value. For a capability selected by the
conversational model, these fields preserve the model-authored tool-use JSON and
the complete JSON result returned to the model, including response guidance.
Internal calls preserve their validated registered input and output values.
Server-owned redaction masks credentials and omits provider-only payloads,
row-level values, and request-local identifiers, but does not replace permitted
household, reform, or result values with structural summaries. The
conversation-scoped activity endpoint applies conversation-read authorization
and server-side visibility filtering; normal mode omits the debug values
entirely.

Context processing uses this existing activity path. Debug input and output
show the exact typed context projection, candidate entities, declarative claims,
validation-generated internal operations, pending requirements, and every
accepted, rejected, conflicted, ignored, or superseding reduction decision.
Fact-resolution records additionally show the source claim, exact catalogue
candidates, model selection and confidence, deterministic
constraint terms, proposed assignment, and final status. Normal activity omits
private operations and their structured values. Invocation traces are diagnostic
records; capabilities do not read them as conversational state.

Activity is not added to the model transcript. The frontend keeps it in
separate state and exposes Debug in the sidebar's user-settings area. A reusable
`useLocalStorage` hook persists one browser-local preference across reloads and
conversations; a missing or invalid value defaults to off. Enabling it retrieves
earlier private calls for the current conversation. In debug mode, each
invocation row is collapsed by default and exposes independently collapsible
input and output JSON trees when present; nested object and array nodes expand
independently.

## Capabilities and retained tools

Public capabilities:

- `policy_information`: ordinary-language catalogue discovery and authoritative
  parameter or variable information.
- `policy_reform`: catalogue-constrained reform resolution and a verified
  `PolicyScenarioRef`.
- `household_analysis`: a typed view over accepted household context facts,
  output-sensitive deterministic requirements and default eligibility,
  requirement-based resumption, post-resolution safe-default application,
  explicit assumption-list reporting, validation, calculation, and a
  `HouseholdResultRef`.
- `society_analysis`: verified policy scenario, fixed-dataset simulation,
  mandatory aggregates, requested aggregates, and a
  `SocietyAnalysisResultRef`.
- `analysis_follow_up`: explanation or extension of one compatible retained
  household or population result without parsing assistant prose.
- `society_chart`: deterministic chart creation from a compatible retained
  population result, with a verified population rerun when required.

Private runtime capability:

- `conversation_relevance`: bounded every-turn scope assessment.

The retained deterministic operations remain typed tools:

- Catalogue discovery and lookup: `list_entities`, `search_variables`,
  `get_variable`, `search_parameters`, `get_parameter`, `list_reform_targets`,
  `list_household_input_variables`, `list_society_output_variables`, and
  `list_supported_outputs`.
- Validation: `validate_reform` and `validate_household` are private.
- Calculation: `run_household_simulation` and `run_society_simulation`.
- Aggregate derivation: `compute_budgetary_impact`,
  `compute_program_breakdown`, `compute_decile_impacts`,
  `compute_winners_losers`, `compute_poverty_metrics`,
  `compute_inequality_metrics`, and `aggregate_result`.
- Presentation: `generate_chart`.

Capability-specific and runtime tools add `assess_relevance`,
`propose_context_change`, `validate_context_change`,
`resolve_context_change`, `apply_context_change`, `reduce_context_patch`,
`resolve_reform`, `assemble_household_candidate`, `select_supported_outputs`,
`extract_result_findings`, and `verify_numerical_response`.

`resolve_reform` performs catalogue search, one structured candidate decision,
private deterministic validation, and at most one representation-only
correction. It cannot introduce an unreturned catalogue path or change policy
parameter path, operation, value, unit, effective date, population, or
jurisdiction. The model tool schema restricts `meaning.parameter_path` to the
paths returned by the catalogue, while friendly labels are derived from the
catalogue by the server. A mismatch between the semantic parameter path and the
reform mapping receives at most one internal representation correction and then
fails as inconsistent structured output; it is never presented as a user
clarification. Genuine semantic uncertainty returned by the resolver still
produces a focused clarification.

## Input precedence and calculation behavior

Policy information, policy reform, household analysis, and population analysis
select year in this order: explicit current input, an explicitly referenced
compatible artifact, then the fixed server default `2026`.

For a household tax, benefit-impact, or entitlement request, the conversational model invokes
`household_analysis` with the available evidence before asking household-input
questions. Shared context processing has already proposed facts from the exact
current message, and deterministic reduction has accepted only valid registered
assertions. A model-authored capability description is not evidence authority
and cannot add an omitted fact.

`FactDefinition` is immutable registered metadata: canonical key and version,
permitted subject types, value shape, cardinality, temporal semantics,
update policy, plain-language label, sensitivity classification, and optional PolicyEngine
binding. It never states that a fact is required, supplies a default, or owns
question wording. The default registry covers the current household adapter's
ages, employment, self-employment and pension income, childcare expenses,
household membership and relationships, children, rent, Council Tax, UK
country, policy year, requested outputs, and reform instruction. It also
retains medical expenses as a conversational fact even though the current
household calculation adapter does not consume it.

Requested-output extraction records only explicitly named calculation metrics.
Generic scope phrases such as “societal impact”, “society-wide impact”,
“population impact”, and “overall impact” select population analysis and its
default output profile; they do not create an `analysis.requested_outputs` fact.

The registry may materialize an additional fact definition only from a verified
PolicyEngine catalogue record. Its key is derived from the catalogue entity and
variable name, and its `engine_binding` retains that exact pair. It must never
create an utterance-specific concept such as a household-income-total field.
`HouseholdEngineFactProjector` passes accepted engine-backed facts into the
matching person, benefit-unit, or household simulation input without enumerating
variable names. Values already assembled by the household input contract take
precedence. Generated definition keys deterministically encode the verified
catalogue entity and variable name, so the registry restores definitions used by
persisted facts after a process restart. This makes a newly catalogue-verified
input usable by deterministic validation and calculation without adding a
field-specific answer handler.

`ContextFact` is an immutable assertion against a stable person, household, or
policy-scenario entity in a specific scope. It records a present typed value or
explicit absence, source-turn evidence, its introduction revision, and the fact
it supersedes when the user makes a correction. Unknown, explicit absence, and
numeric zero are distinct. Stable entity identifiers do not depend on list
positions. Aliases such as the user, a spouse, a name, or a relationship resolve
to those identifiers; PolicyEngine `people[N]` positions are assigned only when
constructing a calculation request and the mapping is retained in artifact
provenance.

Most personal and household facts require an explicit correction before a
different value supersedes them. `analysis.requested_outputs` instead replaces
the prior value whenever the current user message explicitly asks for another
output, because a new calculation request is not a correction of personal data.

`HouseholdInputResolver` is the single deterministic owner of household field
requirements, conditional dependencies, default eligibility, and clarification text.
It reads `HouseholdContextView`, compatible artifact evidence, and
invocation-local defaults in that precedence order, then evaluates every
requirement activated by the resolved requested outputs. One `NeedsInput` result returns all currently
knowable required questions together without enumerating defaults that have not
yet been applied. Missing rent and Council Tax information is consequential for a
general benefit calculation, so the user must provide each amount or explicitly
state that the cost does not apply. Tax-only calculations do not request those
housing values solely to fill the wider household schema. Requested outputs use
explicit ordinary-language aliases or an exact authoritative catalogue match;
in particular, `total tax` resolves to `household_tax`, never `total_wealth`.
Household analysis uses the model-authored `requested_outputs` when present,
and combines them with supported outputs explicitly named in the exact current
message before catalogue resolution. Ordinary-language values are resolved to
canonical output identifiers before requirements are derived. Generic `tax`
expands to `income_tax`, `national_insurance`, and `household_tax`; a waiting
invocation retains only those canonical identifiers. The active typed
requested-output fact is used only when neither the model input nor exact
message identifies an output. The default net-income output is used only when
none of those sources identifies an output.
For a first-person tax-only request with exactly one sterling income amount and
no source or frequency, the household input resolver uses a documented annual
employment-income default. It retains default provenance, reports the
interpretation in the completed assumption list, and permits later correction.
Multiple adults require an explicit couple or civil-partnership answer;
ambiguous income ownership or frequency is never defaulted.

Pending household clarification state retains its exact unresolved
`FactRequirement` values, context scope and revision, and resolved requested
outputs. Each requirement states the registered fact key, stable subject or
subject type, expected answer shape, and whether explicit absence is allowed. A
later clarification cannot replace requested outputs with a shorter
model-authored summary. Shared context processing uses these requirements to resolve
direct answers such as an age, a named spouse's value, or “neither” for two
applicable housing-cost questions. There are no production field-specific
answer handlers. A different output request starts a separate invocation or
follows the completed calculation.

Waiting household state stores its capability request, resolved outputs,
context scope and revision, typed invocation-local defaults, authoritative user
messages needed for completeness checking, and unresolved requirements.
Every persisted `PendingQuestion` contains a typed
`CapabilityInvocationReference` to that waiting state. The reference identifies
the capability, capability version, invocation, context scope, and source
context revision; the waiting record carries the same scope, revision, and exact
requirements. The question status is `awaiting_answer` until deterministic
context reduction finds its typed requirements satisfied, then
`answer_received` until the linked capability completes or produces a revised
question. `ChatTurnService` validates that relationship before saving a
pending question. Provider tool-use identifiers are transport values only and
never become question, fact-resolution, or capability-invocation identifiers.
The model-safe context projection omits both question and invocation identifiers,
and the `household_analysis` model input contains no resumption identifier.
Household analysis selects the unique compatible waiting invocation from the
current context scope and pending requirements. If several remain compatible,
it asks the user to identify the request using their descriptions rather than
exposing internal identifiers. A malformed later outcome never deletes an
existing valid pending question, and completion removes only questions whose
specific waiting invocation has completed.
Accepted facts and their provenance remain in `ConversationContext` instead of
being duplicated in a household-specific evidence record. Invocation defaults
remain separate from user facts and survive clarification; accepted context
facts and compatible artifacts override them. Known values are not reconstructed
from earlier prose. A later question is valid
only when new evidence activates a previously unknowable conditional
requirement, conflicts with retained evidence, or deterministic validation
finds a combination that could not have been rejected earlier. After all
consequential questions are resolved, household assembly applies documented safe
defaults. A completed answer must enumerate every material applied default,
including the default year and current-policy baseline when applicable, as concise
plain-language Markdown bullets under one `Assumptions used` heading. The bullets
use catalogue-backed presentation labels and do not repeat first-person openings.
When household analysis completes, the conversational response reports the
calculated outputs in that turn before offering optional follow-up analysis. It
must not ask the user to choose an output as if the completed capability were
still waiting for input, and it must not claim an amount, entitlement, or
ineligibility for a benefit that is absent from the completed outputs.

Before household validation or simulation, the capability compares sterling
amounts in the invocation's authoritative user messages with validated input
facts. An amount covered by a documented invocation default is included in
those input facts. If a material amount has disappeared during context processing
or resumption, the capability returns `NeedsInput` asking the user to identify
the amount and never substitutes zero. Validated monetary inputs are also
included in numerical narration facts, so the response verifier permits the
answer to identify its calculation basis while continuing to reject unsupported
calculated values.

Every population calculation includes the versioned default aggregate profile:

- budgetary impact;
- winners, losers, and unchanged;
- income-decile impacts using `household_net_income`.

Supported `requested_outputs` are additive and deduplicated against those
defaults. Ambiguous or unsupported requests are retained as typed issues and
must not be described as calculated. Every rerun recalculates the complete
default profile plus supported additions.
The output selector also treats normalized generic population-scope phrases as
the default profile without producing an unsupported-output issue. This
provides deterministic handling for older retained context or imperfect model
extraction.

Population simulations omit the dataset selector when calling policyengine.py's
managed-data API. The installed policyengine.py release therefore chooses its
certified default UK dataset and resolves the corresponding URI and revision.
UK Chat does not contain a dataset name or URI and does not enable unmanaged-data
loading. It contains one presentation-only constant for the friendly dataset
title. Complete simulation objects and any record-level arrays exist only in the
request-local `TurnResultStore`. Durable population artifacts contain aggregate
values plus typed dataset provenance from policyengine.py: the logical dataset
name, data-package name and version, dataset revision, checksum, and certification
metadata. Model-visible results and later-turn artifact summaries include that
provenance and the friendly title. There is no shared record-level population
result cache.

Each population derivative has an explicit validated projection into the
durable aggregate artifact. Validation rejects non-finite values, incomplete or
duplicate decile/group collections, out-of-range shares and rates, and invalid
winner/loser totals before the result reaches the conversational model. The
projection assigns metric-specific units and dimensions; it does not infer them
by recursively inspecting field names.

## Transferable state and persistence

Every conversation has one logical typed `ConversationContext` aggregate in
addition to the full transcript. The context contains stable entities, scopes,
immutable facts, pending requirements, focus, and compatible artifact
references. It is operational memory for validated inputs; the transcript is
the narrative and evidence record; transferable artifacts are verified
calculation results. They do not replace one another. Direct facts and resolved
claims from one current message are validated into one optimistic revision before
persistence and are never written as separate context revisions.

Transferable artifacts are immutable, versioned Pydantic models:
`PolicyScenarioRef`, `HouseholdRef`, `HouseholdResultRef`,
`SocietyAnalysisResultRef`, and `ChartArtifactRef`. Consumers check the
compatibility fields they declare, including schema version, year, scenario
revision, catalogue version, dataset version, and calculation-engine version.

Artifacts and validated waiting input persist independently for a conversation.
There is no conversation-wide analysis phase or single active calculation.
Multiple incomplete invocations may coexist, resume, or branch. Household
resumption selects the pending invocation referenced by typed conversation
context, may recover the only compatible legacy waiting invocation, or must ask
the user to choose when more than one remains compatible. The conversational
model does not preserve or submit an opaque invocation identifier. A clearly
separate household request uses a new invocation and leaves prior state intact.
Household artifacts record their
context scope and revision. The runtime selects the most recent compatible
artifact for the active household scope; an explicit artifact identifier is
needed only for a selected historical branch.

The capability repositories use the same SQLModel engine and existing
`DATABASE_URL` as saved conversations. Alembic owns the SQLModel conversation
and capability schema. Revision `0001` represents the pre-branch conversation
table; revision `0002` adds artifact, waiting-invocation, sanitized-trace, turn-
receipt, and call-receipt tables without conversation foreign keys. The absence
of those foreign keys is intentional because an idempotency receipt may be
created before the conversation row is saved.
Revision `d97a20592837` adds structured invocation debug projections. Revision
`9526d8c80914` adds `conversation_contexts` with its schema version, optimistic
revision, validated JSON payload, and timestamps. It was generated with
`alembic revision --autogenerate`, uses the same `DATABASE_URL` database, and
does not backfill transcripts or superseded analysis records.

Migration commands require `ALEMBIC_DATABASE_URL`. Docker Compose runs
`alembic upgrade head` before starting the backend, and deployed Modal workflows
invoke the migration function before deployment. Backend startup validates that
PostgreSQL is at the repository revision and never creates or alters tables.
Temporary SQLite databases remain available only as isolated repository-test
fixtures and may use `SQLModel.metadata.create_all` inside those fixtures.

Supabase Auth, billing tables, and row-level security remain under the
provider-specific `supabase/migrations/` history. Alembic excludes those objects
and superseded `analysis_*` tables from comparison. Conversation deletion
explicitly removes the conversation's typed context and capability records.

Turn and externally significant call receipts use caller-supplied identifiers
plus canonical input fingerprints. Exact retries report processing or replay a
completed public result. Reuse with different conversation, turn, operation, or
input fails before model or calculation work. Billing claims the turn receipt
once.

## Narration and numerical verification

Capability output supplies validated typed results; it does not impose a fixed
response layout. The conversational model writes ordinary Markdown using the
complete history. Verifier-only `narration_facts` and `narration_fallback`
fields remain server-side and are omitted from the capability result inserted
into model history so an emergency fallback cannot become the model's default
wording. Non-calculation follow-ups are not passed through numerical checks.

When completed quantitative capability facts are used, `verify_numerical_response`
checks currency, percentages, sign, scale, and displayed rounding. An
unsupported numerical draft receives one free-form correction attempt. The
correction preserves natural prose but must remove unsupported arithmetic
totals, differences, rates, or other values that were not returned as verified
facts. If the corrected draft still contains unsupported expressions, the
runtime removes only the affected sentences or Markdown lines and verifies the
remaining prose again. It returns the deterministic fact summary only when no
safe prose remains.

Population analysis and follow-ups over a population artifact are not passed
through `verify_numerical_response`. Their numerical values have already passed
the output-specific aggregate validation described above, and the conversational
model receives those complete validated outputs to summarize in ordinary
Markdown. This exception also disables whole-response numerical verification on
a turn that combines population analysis with another capability.

A clarification-only response has no calculated fact set and does not invoke
`verify_numerical_response`. `ClarificationNarrationGuard` permits natural prose
and Markdown ordered-list markers, but replaces a draft containing an unsupported
currency, percentage, or other substantive number with the capability's exact
clarification. Single-person clarification text, reportable fact labels, and
applied-assumption text use direct or subjectless wording rather than an
internal one-based person index.

After a capability returns `needs_input` or `unsupported`, that capability is
omitted from later model requests in the same user turn. If a provider still
returns another call, the runtime reuses the original typed outcome without
executing the capability or creating a synthetic failure. Internal retry
instructions therefore cannot be added to user-visible fallback text. The next
user turn restores the capability because it may contain new evidence.

Do not expose row-level survey records or a general Python execution operation.
Household examples must be illustrative. Population statements must derive
from aggregate tools and retained aggregate artifacts.

## Verification

Run focused tests while changing a boundary, then the repository checks:

```bash
make typecheck-backend
make test-backend
make test-frontend
make eval-ai-offline
make test
```

Disposable PostgreSQL repository coverage is enabled with
`CAPABILITY_TEST_POSTGRES_URL`. That database must be disposable because the
migration lifecycle test downgrades and recreates SQLModel-owned tables. Follow
`docs/engineering/skills/database-migrations.md` for revision generation and
schema verification. Managed population data and live Anthropic evaluations
remain manual and require their documented credentials and enable flags.
