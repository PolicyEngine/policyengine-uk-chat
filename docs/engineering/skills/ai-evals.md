# AI Evals

Use this skill when adding, reviewing, or running AI evaluation cases for
the UK chat pathway.

## Scope

- AI eval cases live under `evals/`.
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
- Mark Anthropic-only baseline cases with `requirements: [live_model]`; these
  must skip in offline mode and run only through `make eval-ai-live`.
- Use `messages` on trajectory or tool-loop cases when the expected behavior
  depends on prior conversation turns.
- Use `charts_mode: true` when the chart-mode directive is part of the behavior
  under test.
- Use deterministic graders first: JSON partial match, path checks, numeric
  tolerance, forbidden terms, required caveats, privacy statements, and grounded
  number checks.
- `expected_tools` is an exact ordered sequence. Declare every expected call;
  unexpected extra calls fail the case.
- Use `expect.required_values` when final prose must include a numeric result
  from a named tool result. Specify the result path, occurrence, tolerance, and
  nearby required context. `grounded_numbers: true` automatically permits
  numeric leaves from typed tool results; use `allowed_numbers` only for
  legitimate derived values or prompt inputs that are not returned.
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

Runs schema validation, deterministic tool-contract evals, fake-provider
trajectory/answer cases, and fake-provider tool-loop cases with deterministic
tool execution. PR CI runs this command on every change. The 2026 exact-value
household and reform canaries live in
`evals/cases/tool_contract/uk_2026_goldens.yaml`.

```bash
ANTHROPIC_API_KEY=... make eval-ai-live
```

Runs every model-invoking case through the live provider three times, using the
same production model-selection function as UK Chat. Missing runtime
requirements fail rather than skip. Reports under `evals/reports/` include the
routed model for each trial, pass@1, and pass^3. A case contributes to pass^3
only when all three independent trials pass.

The `Live model evals (3 trials)` PR job runs this command whenever chat,
configuration, engine, eval, gateway, prompt, tool, or deployment model paths
change. It is intentionally the full live suite, not a smaller PR subset and
not a nightly job. `--model` remains available for explicit debugging; CI omits
it so production routing is exercised.
