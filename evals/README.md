# UK Chat AI Evals

This directory contains manual evaluation cases for the UK chat AI pathway.
They are not run in PR CI by default.

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

Runs the same cases against the configured live provider. Reports are written to
`evals/reports/`, which is ignored by git.

Set `EVAL_REPORT_DIR` to write reports somewhere else, such as a persistent
volume used by the internal dashboard.

Set `RUN_DATA_EVALS=1` to include cases that require local microdata.
Cases marked `requirements: [live_model]` are skipped offline and run only
through `make eval-ai-live`.

## Suites

- `tool_contract`: deterministic tool behavior through `execute_tool`.
- `trajectory`: prompt to tool choice and tool arguments.
- `answer`: frozen tool output to final prose.
- `tool_loop`: prompt through model tool calls, deterministic tool execution,
  and final prose.

Trajectory and tool-loop cases can set `messages` for multi-turn transcripts
and `charts_mode: true` to test the chart-mode directive.

Source-synced `policyengine-uk` cases with `compiled_coverage_gap` skips are
kept in the suite as visible compiled-backlog markers. Remove the skip only
after `policyengine-uk-compiled` supports the upstream case through the chat
tool contract.

Reports include run-level, suite-level, case-level, and phase-level timing.
Timing is informational in this framework; it is intended for comparison and
regression review rather than pass/fail gating.

The `/evals` frontend page reads stored report JSON through the protected
`/eval-runs` API. Set `EVAL_DASHBOARD_TOKEN` on the backend to enable that API.
