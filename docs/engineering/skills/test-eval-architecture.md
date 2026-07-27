# Test and Eval Architecture

This document explains the UK Chat verification layers, what each layer proves,
and how they should appear in CI. It distinguishes deterministic tests from AI
evals because they have different inputs, failure modes, and ownership.

## Terms

- A **test** executes deterministic application code and expects the same result
  for the same code and fixtures.
- A **tool-contract eval** executes a public PolicyEngine tool and grades its
  structured output. It is deterministic even though it is authored as YAML.
- A **scripted model contract** uses a `FakeModelClient` response authored in
  the case YAML. It tests the harness, expected boundary behaviour, and—where
  applicable—real tool execution between scripted turns. It does not test a
  live model.
- A **live-model eval** sends the case to the configured model. It measures
  model behaviour and must tolerate semantically equivalent output while
  remaining strict about safety and calculation boundaries.

## Current Structure

The counts below are a snapshot of `test/uk-chat-regression-gates` on
2026-07-27. They are included to make the current boundaries concrete, not as
permanent target counts.

| Layer | Current inventory | What it evaluates | How it evaluates | Determinism and dependencies | Current CI location |
| --- | ---: | --- | --- | --- | --- |
| Backend Python tests | 19 modules; 253 declared test functions/methods | Python units, prompt and tool schemas, discovery, simulation adapters, derivatives, gateway policy, model routing, API/SSE behaviour, persistence, billing, observability, and eval harness code | Pytest calls functions or the FastAPI app. Network, database, and model seams are mocked unless the test is explicitly opt-in. | Deterministic. Requires installed backend and policyengine.py dependencies. | `Backend pytest` |
| Deterministic chat HTTP smoke | Included in `backend/tests/test_api.py` | Production FastAPI/SSE orchestration plus a real PolicyEngine household calculation | A controlled Anthropic stream emits a known tool call; the real public tool executes; the test asserts the SSE response and fixed 2026 values. | Deterministic. No live model or browser. | `Backend pytest` |
| Frontend tests and build | 5 test files; 24 declared tests, expanding to 35 Vitest cases | Backend URL resolution and chart parsing/rendering | Vitest/Testing Library with coverage, followed by a production Next.js build. | Deterministic. No browser E2E. | `Frontend tests and build` |
| Tool-contract evals | 69 YAML cases | Public tool names, inputs, schemas, errors, charts, privacy boundaries, PolicyEngine calculations, and exact/range-based 2026 results | `execute_tool()` runs against typed YAML inputs; deterministic graders check partial JSON, paths, chart JSON, and numeric tolerances. Nineteen cases are source-synchronised from `policyengine-uk`; ten are dedicated 2026 goldens. | Deterministic. Most require policyengine.py; three managed-data cases skip without data credentials; one upstream coverage-gap case is an explicit skip. | `Deterministic eval — PolicyEngine tool contracts` |
| Scripted trajectory contracts | 36 YAML cases in `evals/cases/trajectory/` | The intended first model action: whether a tool should be called, which tool, and important input fields | The authored `offline_response` supplies tool calls to `FakeModelClient`; `grade_tool_calls` checks those calls against the case expectation. | Deterministic. Tests the case specification and grader, not live tool-selection ability. | `Deterministic eval — scripted trajectories` |
| Scripted answer contracts | 19 YAML cases in `evals/cases/answer/` | Required facts, grounded values, privacy language, neutral language, error explanations, and forbidden claims | The authored `offline_response` supplies prose; text graders check required/forbidden content and numeric grounding against frozen tool outputs. | Deterministic. Tests the answer contract and grader, not live prose generation. | `Deterministic eval — scripted answers` |
| Scripted tool-loop contracts | 7 YAML cases in `evals/cases/tool_loop/` | Multi-turn orchestration such as discover-then-retrieve, calculate-then-chart, validation, recovery, and final grounding | `FakeModelClient` supplies each model turn. The harness executes the requested public tools between turns and feeds their real outputs into the next scripted turn. | Deterministic model decisions with real deterministic tool execution. Does not test whether a live model chooses those turns. | `Deterministic eval — scripted tool loops` |
| Live gateway evals | 15 YAML cases in `evals/live/gateway/` | Opening-turn classification, route, selected tool, gating slots, and extracted plan | Each prompt goes through `run_gateway`; typed gateway results are graded against outcome and slot expectations. | Live Anthropic call. Model-facing PRs run three trials per case. | `Live gateway evals (3 trials)` |
| Live trajectory evals | 1 YAML case in `evals/live/trajectory/` | The live model's first tool-selection turn and tool arguments | The harness sends the production system prompt and public tool definitions directly to the selected model, then grades the returned calls. It does not execute tools in this suite. | Live Anthropic call. Three trials per case. The harness currently bypasses gateway-plan injection. | `Live trajectory evals (3 trials)` |
| Live answer evals | 4 YAML cases in `evals/live/answer/` | Live prose grounded in frozen tool results | Frozen results are serialised into a second user message; the selected model answers without tools; live-specific graders inspect the response. | Live Anthropic call. Three trials per case. This message shape is an eval seam, not the production tool-result transcript. | `Live answer evals (3 trials)` |
| Live tool-loop evals | 2 YAML cases in `evals/live/tool_loop/` | Live model planning, real tool execution, recovery, and final prose across multiple turns | The selected model can call public tools for up to four iterations. Every call is executed through `execute_tool`, returned to the model, and the complete trace and final answer are graded. | Live Anthropic plus deterministic PolicyEngine execution. Three trials per case. The loop currently bypasses the production gateway and HTTP/SSE route. | `Live tool-loop evals (3 trials)` |
| Enhanced FRS integration | 1 lifecycle test | Managed dataset resolution and a full 2026 society simulation followed by budget, programme, decile, winners/losers, poverty, inequality, and aggregate derivatives | Pytest downloads the configured Enhanced FRS dataset and asserts identities, finite values, shapes, and policy-specific ranges. | Deterministic calculation with external managed data and credentials. | `Configured Enhanced FRS integration` |

There are 153 YAML eval cases in total:

- 69 deterministic tool-contract cases;
- 62 scripted model contracts: 36 trajectory, 19 answer, and 7 tool-loop;
- 22 separate live-model cases: 15 gateway, 1 trajectory, 4 answer, and 2
  tool-loop.

The roots are disjoint by construction:

- deterministic mode loads only `evals/cases/`;
- live mode loads only `evals/live/`;
- deterministic model cases must contain scripted responses;
- live cases must require `live_model` and cannot contain scripted responses;
- deterministic and live case IDs must not overlap.

## Why the Separation Is Enforced

The first PR #221 implementation incorrectly sent the 62 scripted model
contracts to the live provider in addition to the 20 explicitly live cases.
That invalid 246-trial run produced:

| Suite | Cases | Trials | Passed trials | Failed trials |
| --- | ---: | ---: | ---: | ---: |
| Gateway | 15 | 45 | 22 | 23 |
| Trajectory | 37 | 111 | 30 | 81 |
| Answer | 23 | 69 | 4 | 65 |
| Tool loop | 7 | 21 | 0 | 21 |
| **Total** | **82** | **246** | **56** | **190** |

Anthropic completed the calls. There was no credential, provider, dependency,
or rate-limit failure. The 190 failed trials produced 401 grader messages
because one trial can violate more than one assertion:

| Grader message family | Count | Typical meaning |
| --- | ---: | --- |
| Missing exact required text | 118 | The answer omitted one required substring, often while expressing the same concept differently. |
| Tool at wrong exact position | 70 | The model used discovery, validation, or recovery before the expected tool. |
| Wrong exact tool-call count | 58 | The trace had an additional discovery/retry call or stopped to ask a question. |
| Tool-input mismatch | 56 | A required value differed, was omitted, or was represented in another shape. |
| “Ungrounded” number | 42 | The number came from a prompt/tool input or a derivation rather than a numeric leaf in the tool output. |
| Gateway outcome mismatch | 18 | The live classifier selected a different outcome enum. |
| Forbidden exact text | 15 | The answer included a prohibited substring or a value the scripted fixture expected it not to retrieve. |

The core cases contained scripted turns and matching deterministic assertions.
Live mode discarded each scripted turn while retaining the assertion authored
for it. Tightening tool-call grading from an ordered subsequence to an exact
sequence further changed the deterministic contract.

That means the first run mixed three different findings:

1. valid live responses rejected by fixture-shaped assertions;
2. stale or contradictory eval contracts;
3. genuine model, prompt, gateway, or tool-schema weaknesses.

That run is not a live-model baseline and must not be used for thresholds. The
separate live suite adds coverage without changing or reinterpreting any
scripted case.

## What Each Eval Assertion Means

| Assertion | Appropriate use | Inappropriate use |
| --- | --- | --- |
| Exact structured value or numeric tolerance | Tool outputs, required schema fields, fixed 2026 goldens, gateway enums, and values copied from a named tool result | Free-form wording or formatting |
| Exact tool sequence | A sequence that is itself the product contract, such as calculate-then-chart when no discovery or recovery call is permitted | The default for every live trajectory; a valid discovery or error-recovery call should not fail merely because it was additional |
| Required tool or ordered tool subsequence | Confirming that a calculation, validation, or chart actually happened while permitting declared discovery/recovery calls | Cases where any extra tool would be unsafe |
| Required input fields | Load-bearing values such as year, reform amount, household income, or the sole valid country enum | Optional empty objects whose omission has the same runtime meaning as `{}` |
| Required text | Short user-facing terms that are contractual and difficult to paraphrase, such as a required privacy disclosure concept | Whole sentences, one currency formatting style, SDK implementation names, or a single exact paraphrase |
| Forbidden text or tools | Fabricated values, value judgements, row-level data claims, unsafe tools, or prohibited calculation paths | Broad substrings that can occur harmlessly inside another word |
| Grounded numbers | Values returned by named tools, prompt/input facts, or explicitly declared deterministic derivations | Treating every number absent from output leaves as fabricated |
| Semantic outcome | Privacy refusal, request for clarification, neutral explanation, or successful recovery | Enforcing one authored sentence as the only acceptable expression of the outcome |

Scripted model contracts should have intentionally simple fixture responses.
Their purpose is to make the grader and deterministic orchestration contract
fail for a known reason when the contract changes. Passing a scripted case says
that the fixture satisfies the declared contract; it does not show that the
production model will produce that fixture.

A scripted case must never be executed as a live case. When the same scenario
deserves live coverage, add a new case with a new ID under `evals/live/` and
author live-specific assertions. In particular:

- permit declared discovery and recovery calls unless their absence is the
  behaviour under test;
- normalise or numerically parse currency rather than requiring one display
  format;
- use alternatives or structured semantic checks for paraphrasable prose;
- ground numbers against relevant prompt inputs, tool inputs, tool outputs, and
  declared deterministic derivations;
- select the successful occurrence when a tool is retried;
- remove expectations tied to obsolete SDK APIs or internal implementation
  names.

## CI/CD Split

Keep the complete three-trial live suite on model-facing pull requests, but
separate it by boundary so a check name identifies what regressed.

| Proposed required check | When it runs | Contents | Why it is separate |
| --- | --- | --- | --- |
| `Backend deterministic` | Every PR | Backend pytest, including deterministic HTTP/SSE smoke and eval-harness unit tests | Application correctness and coverage should not be obscured by YAML eval failures. |
| `Frontend deterministic` | Every PR | Vitest coverage and production build | Frontend failures have different owners and diagnostics. |
| `PolicyEngine tool contracts` | Every PR | Source-sync check plus all non-data tool-contract cases, including 2026 goldens | Shows whether public calculations, schemas, and fixed values changed without involving a model. |
| `Scripted trajectory contracts` | Every PR | The 36 `trajectory/core.yaml` cases with `FakeModelClient` | Makes prompt/tool-boundary specifications visible without claiming live-model coverage. |
| `Scripted answer contracts` | Every PR | The 19 `answer/core.yaml` cases with `FakeModelClient` | Isolates answer grader and wording-contract drift. |
| `Scripted tool-loop contracts` | Every PR | The 7 `tool_loop/core.yaml` cases with scripted turns and real tools | Isolates deterministic orchestration and result-passing failures. |
| `Enhanced FRS 2026 integration` | Every PR | Credentialed managed-data lifecycle and range assertions | Separates data availability/calculation drift from ordinary unit and model failures. |
| `Live gateway evals (3 trials)` | Model-facing PRs | All 15 gateway cases | Reports routing and plan-extraction stability directly. |
| `Live trajectory evals (3 trials)` | Model-facing PRs | Only cases under `evals/live/trajectory/` | Reports live tool-selection and argument stability directly. |
| `Live answer evals (3 trials)` | Model-facing PRs | Only cases under `evals/live/answer/` | Reports grounded answer quality separately from tool choice. |
| `Live tool-loop evals (3 trials)` | Model-facing PRs | Only cases under `evals/live/tool_loop/` | Reports end-to-end model/tool recovery without hiding it among first-turn or prose failures. |

The live jobs can use a CI matrix over `gateway`, `trajectory`, `answer`, and
`tool_loop`, with a separately named report artifact per suite. Splitting the
jobs does not reduce coverage: the union remains the complete separate live
suite, with three independent trials per case.

Each live job should report:

- per-case results for all three trials;
- pass@1 and pass^3 for that suite;
- the routed model;
- observed tool traces where applicable;
- failures grouped into model behaviour, grader mismatch, tool execution
  failure, and provider/infrastructure failure.

The required threshold must be explicit. The current runner exits non-zero for
any failed trial, so pass@1 and pass^3 are reported but are not used as
thresholds. Critical cases may deliberately require all three trials to pass;
broader suites should use an agreed suite-level threshold rather than acquiring
one accidentally from the runner implementation.

## Browser Coverage

Browser E2E is intentionally out of scope. The deterministic FastAPI/SSE smoke,
frontend component tests, and production frontend build cover the current
simple UX without adding a browser-dependent CI layer.
