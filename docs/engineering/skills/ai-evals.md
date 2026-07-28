# AI Evals

Use this skill when adding, reviewing, or running AI evaluation cases for
the UK chat pathway.

See `test-eval-architecture.md` for the complete test/eval matrix, what each
suite proves, and how the suites should be presented as separate CI checks.

## Scope

- Deterministic eval cases live under `evals/cases/`.
- Separate live-model eval cases live under `evals/live/`.
- The reusable harness lives under `backend/eval/`.
- Production chat code must not import evaluation modules.
- Deterministic evals run on every pull request.
- Pull requests that change model-facing paths run the complete live-model
  suite with three independent trials per case. There is no separate nightly
  suite.

## Suite Boundaries

- `tool_contract`: deterministic public tools through `execute_tool`.
- `trajectory`: user prompt to model tool calls and arguments.
- `answer`: frozen tool output to final prose.
- `tool_loop`: user prompt through one or more real tool executions to final
  prose.

The same case must never run in both layers. Deterministic trajectory, answer,
and tool-loop cases require scripted model responses. Live cases require
`live_model`, cannot contain scripted responses, and must have distinct IDs.
When a deterministic scenario also needs live coverage, create a separate case
under `evals/live/`.

Keep cases focused on one boundary. If a case needs to check both tool choice
and final prose, split it into one trajectory case and one answer case.
Use `tool_loop` only when the seam being tested requires deterministic tool
execution between model turns, such as calculate-then-chart or
validate-then-calculate flows.

## Case Rules

- Author cases in YAML and validate them through the Pydantic schemas.
- Use partial JSON expectations instead of full snapshots unless the complete
  output shape is the contract being tested.
- Put frozen tool outputs in `evals/fixtures/tool_outputs/` when more than one
  case may use them.
- Mark cases requiring local microdata with `requirements: [data]`.
- Mark cases requiring the policyengine.py UK packages with
  `requirements: [policyengine_py]`.
- Mark every case under `evals/live/` with `requirements: [live_model]`.
- Use `messages` on trajectory or tool-loop cases when the expected behavior
  depends on prior conversation turns.
- Use `charts_mode: true` when the chart-mode directive is part of the behavior
  under test.
- Use deterministic graders first: JSON partial match, path checks, numeric
  tolerance, forbidden terms, required caveats, privacy statements, and grounded
  number checks.
- Set `factual_neutrality: true` for policy answers that must follow the shared
  neutrality contract. Do not copy the policy-value term list into each case.
  The shared matcher intentionally covers progressivity and regressivity word
  families, including `progressively` and `regressively`, while using word-aware
  patterns rather than arbitrary substring matches.
- `expected_tools` is an ordered subsequence. Declare load-bearing calls and
  use `forbidden_tools` for calls that must never occur. Discovery and recovery
  calls may appear between expected calls.
- Use `expected_tool_results` in tool-loop cases when the executed tool output
  itself must satisfy a partial schema or numeric range. Select the last
  successful result when retries are allowed. This is separate from
  `expect.required_values`, which proves that final prose reports a value from
  that result.
- Use `expect.required_values` when final prose must include a numeric result
  from a named tool result. Specify the result path, occurrence, tolerance, and
  nearby required context. Prefer this source-aware contract for fiscal or
  multi-output answers: declare every requested headline value explicitly,
  including its scale, sign, and rounding tolerance.
- Use `grounded_numbers: true` only when the answer has a small, predictable
  numeric vocabulary. It automatically permits raw numeric leaves from tool
  inputs and outputs, but it does not infer unit conversions, rounded
  components, or arithmetic derivations. Declare any permitted derivations
  with `allowed_derived_numbers`. Do not use the blanket check for rich fiscal
  prose when `expected_tool_results` and `required_values` already cover the
  calculated outputs.
- In an offline tool-loop fixture, reference one field from a prior tool result
  as `$tool_result.<tool_name>.<field>`. To pass the complete result into an
  object-valued tool input, use `{$tool_result: <tool_name>}`. The harness
  resolves these references before executing the later tool, allowing opaque
  result handles and compact results to cross iterations.
- Source-synced `policyengine-uk` cases are generated from the installed
  `policyengine_uk` package. Update them with `make sync-policyengine-uk-evals`
  rather than editing the generated YAML by hand. Install the eval extras first
  with `pip install -r backend/requirements-eval.txt`.
- Keep source-synced cases with `skip.code: policyengine_py_coverage_gap`
  visible in the suite. These mark upstream cases that should become executable
  as the policyengine.py chat household contract gains support or parity.
- When policyengine.py supports a skipped upstream case through the chat tool
  contract, remove the skip flag, add or verify the output mapping, and rerun
  `make sync-policyengine-uk-evals`.
- Do not delete source-synced skipped cases just because they currently fail
  against the chat tool contract. If a case is no longer relevant upstream,
  update the source manifest with the reason.

## Commands

```bash
make sync-policyengine-uk-evals
```

Regenerates source-synced `policyengine-uk` tool-contract cases from the
installed package.

```bash
make check-policyengine-uk-evals
```

Checks that generated source-synced cases are fresh.

```bash
make eval-ai-offline
```

Runs only `evals/cases/`: deterministic tool-contract evals, fake-provider
trajectory/answer cases, and fake-provider tool-loop cases with deterministic
tool execution. PR CI runs these as separately named checks. The 2026
exact-value household and reform canaries live in
`evals/cases/tool_contract/uk_2026_goldens.yaml`.

```bash
ANTHROPIC_API_KEY=... make eval-ai-live
```

Runs only the separate cases under `evals/live/` through the live provider
three times, using the same production model-selection function as UK Chat.
Missing runtime requirements fail rather than skip. Reports under
`evals/reports/` include the routed model for each trial, pass@1, and pass^3. A
case contributes to pass^3 only when all three independent trials pass.

Model-facing PRs run separate gateway, trajectory, answer, and tool-loop live
jobs. Their union is the full live suite; no deterministic case is sent to the
provider. There is no nightly job. `--model` remains available for explicit
debugging; CI omits it so production routing is exercised.
