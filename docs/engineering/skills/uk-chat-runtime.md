# UK Chat Runtime

Use this skill when changing the UK chat model pathway, system prompts, exposed
tools, calculation behavior, or AI-facing runtime boundaries.

## Source Boundaries

The backend is organized by topic — one package per concern:

- `backend/chat/` owns the chat turn: `orchestrator.py` (request parsing, SSE
  streaming, the tool loop), `system_blocks.py` (system-block assembly),
  `model_selection.py`, `schemas.py`, `titles.py`,
  `suggestions.py`, and `routes.py` (the `/chat` router).
- `backend/gateway/` owns the opening-turn pre-pass: `runtime.py` (the
  forced-tool classifier), `catalogue.py` (the server-side PolicyEngine
  catalogue resolver), `intent.py` (bounded wording extraction),
  `assessment.py` (catalogue-backed exact reform construction), `policy.py`
  (slot ownership + deterministic gate), `execution.py` (dependency-aware
  compute contract), `clarifications.py` (fixed question rendering),
  `proposals.py` (signed stateless confirmation handoff), and `trace.py`
  (internal/eval decision traces).
- `backend/prompts/` owns all product prompt text: `system.py` (the compute
  system prompt), `gateway.py` (gateway + lightweight prompts), `meta.py` (title
  + suggestion prompts). Keep prompts modular and declarative there.
- `backend/tools/` owns the model-facing tool seam: `definitions.py` (schemas),
  `registry.py` (the `@register_tool` decorator), and `dispatch.py` (the
  `execute_tool` dispatcher + tool functions). Register model-facing tools once
  with `@register_tool`; `TOOL_DEFINITIONS` and `TOOL_HANDLERS` are derived from
  that registry. Reuse shared schema fragments in `definitions.py` rather than
  duplicating object/array/dataset/format shapes.
- `backend/engine/` owns the deterministic PolicyEngine compute helpers
  (policyengine.py runtime loading, catalog discovery, households, official
  derivative adapters, reforms, simulations, and serialization).
- `backend/config/` owns model-call configuration (model ids, temperatures, the
  Anthropic client factories, and environment settings).
- `backend/api/` owns the HTTP surface (`main.py` app + router mounting,
  `errors.py`, `rate_limit.py`).

Do not spread prompt strings back into route handlers. If runtime prompt rules
change, update `backend/prompts/` and the prompt contract tests together.

## Model Harness

The current chat runtime is application-specific rather than a generic model
harness. It calls the Anthropic SDK directly for streaming control. Pydantic AI
imports/comments may exist, but they should not be treated as the active
orchestration layer unless the code is deliberately refactored.

Keep model/provider-specific code at the orchestration edge. Durable guidance
for agents belongs in `docs/engineering/skills/`; product behavior prompts
belong in `backend/prompts/`.

## Tool Boundary

Only tools registered with `@register_tool` are exposed to the model and
dispatched by `execute_tool()`. At present, the exposed tools are:

- Discovery: `list_entities`, `search_variables`,
  `get_variable`, `get_variable_definition`, `search_parameters`,
  `get_parameter`, `list_reform_targets`, `list_household_input_variables`,
  `list_society_output_variables`, and `list_supported_outputs`.
- Validation: `validate_reform` and `validate_household`.
- Simulation: `run_household_simulation` for illustrative synthetic households
  and `run_society_simulation` for aggregate society-wide simulations.
- Derivatives: `compute_budgetary_impact`, `compute_program_breakdown`,
  `compute_decile_impacts`, `compute_winners_losers`,
  `compute_poverty_metrics`, `compute_inequality_metrics`, and
  `aggregate_result`. These tools must delegate aggregation and derivation to
  the highest-level public policyengine.py collection helper available. Use
  individual output classes only where no collection helper exists. Runtime
  code must not aggregate MicroSeries, NumPy arrays, or survey weights itself.
- Artifacts: `generate_chart`, including deterministic app-v2-style presets for
  budget waterfalls, programme waterfalls, decile bars, winners/losers stacks,
  poverty/inequality relative bars, and earnings lines.

`compute_decile_impacts` deliberately pins its analytical meaning instead of
inheriting policyengine.py defaults. Its single `decile_concept` input is a
runtime-enforced three-state choice, so callers cannot construct inconsistent
combinations of basis and income measure:

- `household_net_income` measures and ranks by `household_net_income`.
- `equivalised_hbai_net_income` measures and ranks by
  `equiv_hbai_household_net_income`.
- `wealth` measures `household_net_income`, groups by
  `household_wealth_decile`, and uses the household entity.

Delegate the calculation to policyengine.py's public
`calculate_decile_impacts` collection helper. Keep the income variable, decile
variable, entity, and `quantiles=10` explicit at that boundary so dependency
upgrades cannot silently change these meanings. Do not loop over standalone
`DecileImpact` objects in UK Chat: the collection helper prepares and validates
the shared grouping analysis once.

Computed income-decile grouping remains inside policyengine.py, whose default
household grouping uses person-weighted ranks. UK Chat must not materialize
decile assignments or manipulate survey weights locally. Computed groups
exclude households with negative or non-finite values of the selected ranking
income from the final reported deciles, matching country-package reporting.
Wealth deciles continue to use the precomputed `household_wealth_decile` model
variable.

Preserve policyengine.py's missing-value semantics. Empty deciles have null
means and changes, and relative change is null when the baseline mean is zero.
These values must remain null through the tool and chart boundaries: charts
render no bar and display a dash for the missing value, never a zero-valued bar.
Decile chart metadata must carry both the measured income concept and grouping
concept so household-net-income, equivalised-HBAI, and wealth axes cannot be
mislabelled.

`get_variable_definition` is the authoritative answer to what a variable means
and how the model defines it. `backend/engine/definitions.py` owns it, separate
from `engine/discovery.py`, which answers only whether a variable exists and
where it lives. Resolution is deterministic and has exactly four outcomes:
`success` with one definition, `needs_confirmation` with ranked options when
several variables tie at the best matching tier, `error` with ranked
suggestions when nothing matches, and `error` for a query with no word
characters. Matching walks fixed tiers — exact name, exact label, phrase, all
tokens, description — and ties break on shorter canonical name then alphabetical
order, so the result never depends on registry iteration order.

The definition source is the same compiled model version that runs the
simulations, so definitions cannot diverge from calculations. This is the
constraint that blocked issue #140: do not source formulas from a package other
than the one performing the calculation.

`formula` is authoritative only when `formula.available` is true. It is then the
model's own `adds`/`subtracts` composition, reported as an exact statement over
other model variables. Most variables have no machine-readable formula; those
report `available: false` with a note. Never turn a label or description into a
formula, in the tool or in prose.

Helper functions in `backend/engine/` are implementation details unless they
are exposed through `@register_tool`.

`tools.registry.tool_definitions()` returns caller-owned JSON-like snapshots for
model/eval requests. Mutating those snapshots is only a local per-call edit and
does not register, remove, or mutate canonical tools. Use `@register_tool` for
tool registration.

The runtime uses policyengine.py with the UK country package. The default year
is `2026`. Society-wide tools always use the pinned `enhanced_frs_2024_25`
release declared by `UK_CHAT_DATASET` in `backend/engine/constants.py`. This is
an application invariant, not deployment configuration or a model-facing tool
argument. Dataset name and label are derived from that single URI declaration,
and simulation results include the resolved metadata for transparency. To
change the dataset, update the constant and redeploy the application.

The public runtime does not expose row-level survey records or a broad
model-facing Python execution tool. Use discovery and derivative tools rather
than asking the model to write arbitrary code.

Before a society simulation uses variable-level outputs, inspect the model
version's authoritative `entity_variables` defaults through
`list_society_output_variables`. Verify every required non-default variable
with `search_variables` or `get_variable`, then pass only those non-default
names through `extra_variables` under the entity reported by discovery.
`extra_variables` materializes existing model variables; it does not create
expressions, aliases, filters, or derived variables. Use `aggregate_result`'s
official policyengine.py filter arguments for conditional weighted aggregates.

## Deterministic And Non-Deterministic Segments

- Non-deterministic: the opening classifier, catalogue-backed reform
  construction and confidence assessment, compute-model tool use, prose
  generation, follow-up suggestions, and title generation.
- Deterministic: request validation, the gateway gate (criticality + outcome),
  bounded output/reform wording extraction, server slot ownership/defaults,
  execution-plan construction, clarification rendering, proposal signing and
  verification, lightweight-route tool omission, approved-reform enforcement,
  tool dispatch, typed tool execution after selection, derivative calculation,
  chart JSON construction, result truncation/summarisation, billing
  calculation, and database writes.

Before applying gateway criticality, the server completes every schema slot
omitted from a selected tool plan according to ownership. Concrete schema
defaults become `default` with their value; derivative `simulation_id` and
chart `result_id` inputs become `runtime`; only unresolved user-owned choices
become `assumed`. The synthetic requested `output` slot is added when absent.
Thus a classifier omission cannot be mistaken for grounded user intent, and a
runtime handoff cannot trigger a user question. An asserted `prompt` slot must
contain a non-empty value, and an asserted output must name a supported output
concept; otherwise the server treats it as `assumed`. Server ownership also
overrides classifier claims about default and runtime fields.

The gateway treats the supported output vocabulary as authoritative for direct,
static microsimulation. Unqualified requests for cost, revenue, spending,
poverty, inequality, decile effects, winners and losers, caseload, marginal
rates, or net income therefore proceed as modelled outputs; behavioural and
macroeconomic exclusions are result caveats, not implicit user requests. An
unmodellable output can trigger `partial` only when the classifier supplies its
name plus an exact quote from the user that explicitly requests it. The server
normalises case and whitespace, rejects evidence absent from the prompt,
deduplicates accepted limitations, and caps them at four.

Domain and capability are separate structured decisions. Jurisdiction defaults
to `uk_or_unspecified`; `explicit_non_uk` and `unrelated` are accepted only with
an exact quote from the original message. Capability defaults to `supported`;
`catalogue_uncertain` and `explicitly_unmodellable` likewise require an exact
quote. A missing tool without validated unmodellable evidence is uncertainty
(`needs_plan`), not proof that the request is unsupported. This makes refusal a
positive-evidence decision rather than the consequence of classifier doubt.

`needs_plan` is rendered from an allow-list of structured reason codes and
terminates with no response-model call. Unknown or internal reason codes are
not turned into generic questions; orchestration logs them and fails open to
compute, while the society tool guard still refuses an unapproved reform.
`partial`, `out_of_scope`, and `irrelevant` retain the no-tool lightweight
writer. The gateway's non-`ready` outcomes remain structurally enforced by
omitting tools from any model request that occurs.

## First-turn Catalogue Confirmation

On an opening turn, the gateway classifier may emit a bounded set of concise
parameter/reform-target and variable search terms. Every query must be contained
in an exact quote from the original message; invented queries are discarded.
The prompt also forbids removing a foreign jurisdiction from the evidence.
`gateway.catalogue` resolves accepted queries against the installed
`policyengine.py` UK model. This is an internal server lookup, not another
model-facing tool.

Catalogue results are ranked by evidence quality. Exact identifiers, aliases,
labels, and sufficiently specific phrase matches are authoritative; fuzzy or
description-only matches are retained only as suggestions. They never
authorise recovery or override a domain decision. An `irrelevant` decision is
terminal regardless of matches, unresolved queries, or catalogue availability.

An authoritative classifier-stage match confirms only that the model exposes a
candidate concept. When the first plan is UK/unspecified, explicitly
`catalogue_uncertain`, and lacks a tool, the runtime permits exactly one second
gateway call with the server-verified candidates. That call must rebuild the
tool and grounded slots from the original message. The deterministic gate then
applies normally: grounded work proceeds, while an ambiguous load-bearing slot
still asks a follow-up. The recovery call repairs routing only; it does not
approve an executable reform. There is no catalogue/replan loop.

Every fully stated society reform then enters the dedicated reform assessor.
It must search the installed reform-target catalogue before emitting a result,
uses at most four distinct searches, and may use only paths and labels returned
by those searches. The server validates that bindings exactly cover the reform
paths, labels equal catalogue labels, the reform passes PolicyEngine
validation, and directional values do not contradict the grounded action. A
construction with confidence 80 or above becomes the exact approved reform in
the execution plan. Lower confidence becomes `needs_plan` and the deterministic
question names only human-readable catalogue labels. A successful search with
no construction asks the user to identify the specific supported policy
concept. Catalogue unavailability or an invalid/exhausted assessment is a
terminal technical error: it must never fall through to compute with a guessed
or unverified reform.

The execution plan maps requested output to its derivative, orders
`run_society_simulation` before that derivative, includes the current-year
default and product conventions, and carries the validated reform JSON. The
tool context rejects a society reform that differs from that approved JSON.
Internal paths are passed only to the compute context; clarification text uses
human-readable labels.

Low-confidence proposals resume without a chat database. The deterministic
clarification includes a non-rendered, HMAC-signed payload in assistant history,
bound to the session and source-prompt hash and expiring after 24 hours. It is
signed but not encrypted. A stable `GATEWAY_PROPOSAL_SIGNING_KEY` of at least
32 bytes is required in every backend instance. An affirmative follow-up or an
explicit alternative reuses the exact signed construction. Expired proposals
or catalogue-version changes are reassessed; consumed older markers are not
reopened. Markers are stripped before any model call.

Eval-only gateway traces record selected and target tools, slot provenance,
structured reasons, applied defaults, reform confidence/search/bindings,
catalogue version and resolver model, recovery use, and proposal resumption.
Public SSE projection must never include this trace.

Tool choice is model-mediated unless the route layer deliberately forces a
specific tool. Prompt and schema guidance improve selection consistency, but
they are not deterministic controls. Every model call sets its temperature from
`backend/config/`: `DEFAULT_TEMPERATURE` (0, deterministic) for the
compute loop, titling, the gateway classifier, and evals; `SUGGESTION_TEMPERATURE`
for follow-up suggestion chips, which deliberately sample with variety.

## Policy Analysis Rules

- Be factually neutral. Do not call UK tax or benefit choices good, bad, fair,
  unfair, regressive, progressive, generous, punitive, or similar.
- Quantitative policy answers should be computed with the lifecycle tools; do
  not answer tax, benefit, reform, poverty, decile, or distributional questions
  from memory.
- Static parameter questions should use `search_parameters` to discover the
  canonical path, then `get_parameter` to retrieve its value. Do not run
  household or society simulations just to infer a parameter value.
- Use `validate_reform` only when the user is drafting, debugging, or asking
  whether reform JSON is valid. Do not use it as a routine preflight before
  every simulation.
- Do not access, display, quote, or imply access to row-level survey microdata
  or real households.
- Use aggregate simulation and derivative tools only for aggregate outputs.
- Do not row-sample FRS-derived datasets, including Enhanced FRS.
- If a user asks for household examples, construct illustrative synthetic
  households through `run_household_simulation`, and label examples as
  illustrative, synthetic, or hypothetical.
- The household tool supports one household containing one benefit unit. Do not
  combine unrelated adults or multiple benefit units into one tool call.
