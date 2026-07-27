# UK Chat AI Evals

This directory contains evaluation cases for the UK chat AI pathway.
Deterministic cases run on every pull request. Model-facing pull requests also
run the separate live-model suite.

- `evals/cases/` contains deterministic tool contracts and scripted model
  contracts.
- `evals/live/` contains live-model cases only.

The roots are disjoint. Scripted cases are never sent to a live provider.

## Commands

```bash
make sync-policyengine-uk-evals
```

Regenerates source-synced `policyengine-uk` tool-contract cases from the
installed `policyengine_uk` package.

```bash
make check-policyengine-uk-evals
```

Checks that generated source-synced cases are fresh.

```bash
make eval-ai-offline
```

Runs deterministic tool-contract cases plus fake-provider trajectory and answer
cases, plus fake-provider tool-loop cases that execute deterministic tools
between frozen model turns. This checks schemas, runners, and graders without
calling a live model.

```bash
ANTHROPIC_API_KEY=... make eval-ai-live
```

Runs every case under `evals/live/` three times against the configured live
provider with production model routing. Reports include per-trial model IDs,
pass@1, and pass^3 and are written to `evals/reports/`, which is ignored by git.

Set `RUN_DATA_EVALS=1` to include cases that require local microdata.
Every live case must include `requirements: [live_model]` and must not include
an `offline_response` or `offline_responses` fixture.

There is no nightly-only suite and no browser E2E layer. The deterministic
HTTP/SSE integration test controls the model boundary while exercising the
real PolicyEngine tool path.

## Suites

- `tool_contract`: deterministic tool behavior through `execute_tool`.
- `trajectory`: prompt to tool choice and tool arguments.
- `answer`: frozen tool output to final prose.
- `tool_loop`: prompt through model tool calls, deterministic tool execution,
  and final prose.

Trajectory and tool-loop cases can set `messages` for multi-turn transcripts
and `charts_mode: true` to test the chart-mode directive.

Source-synced `policyengine-uk` cases with `policyengine_py_coverage_gap` skips are
kept in the suite as visible policyengine_py-backlog markers. Remove the skip only
after `policyengine` supports the upstream case through the chat
tool contract.
