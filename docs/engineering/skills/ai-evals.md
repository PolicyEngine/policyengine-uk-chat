# AI Evals

Use this skill when adding, reviewing, or running manual AI evaluation cases for
the UK chat pathway.

## Scope

- AI eval cases live under `evals/`.
- The reusable harness lives under `backend/evaluation/`.
- Production chat code must not import evaluation modules.
- Evals are manual for now; do not add them to PR CI unless that product
  decision changes.

## Suite Boundaries

- `tool_contract`: deterministic public tools through `execute_tool`.
- `trajectory`: user prompt to model tool calls and arguments.
- `answer`: frozen tool output to final prose.
- `tool_loop`: user prompt through one or more model/tool turns to final prose.

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
- Mark cases requiring the compiled package with `requirements: [compiled]`.
- Use deterministic graders first: JSON partial match, path checks, numeric
  tolerance, forbidden terms, required caveats, privacy statements, and grounded
  number checks.
- Source-synced `policyengine-uk` cases are generated from the installed
  `policyengine_uk` package. Update them with `make sync-policyengine-uk-evals`
  rather than editing the generated YAML by hand.
- Keep source-synced cases with `skip.code: compiled_coverage_gap` visible in
  the suite. These mark upstream cases that should become executable as
  `policyengine-uk-compiled` gains support or parity.
- When `policyengine-uk-compiled` supports a skipped upstream case through the
  chat tool contract, remove the skip flag, add or verify the output mapping,
  and rerun `make sync-policyengine-uk-evals`.
- Do not delete source-synced skipped cases just because they currently fail
  against compiled. If a case is no longer relevant upstream, update the source
  manifest with the reason.

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
tool execution.

```bash
ANTHROPIC_API_KEY=... make eval-ai-live
```

Runs the same suite through the live provider and writes reports under
`evals/reports/`.
