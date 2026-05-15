# uk-chat evals

Evaluation harness for the chat. Tests two positionings — **supplement** to app-v2 reports and **alternative** to them — against pre-committed thresholds.

The full design (rationale, thresholds, scenario descriptions) lives in [SPEC.md](./SPEC.md). This README covers structure and how to extend.

## What's here today

```
evals/
  SPEC.md                ← the design doc — read this first
  README.md              ← this file
  scenarios/
    a1_*.yaml            ← Test A scenarios (supplement)
    b1_*.yaml            ← Test B scenarios (alternative)
  fixtures/
    pe_api/              ← reference PE-API responses for Test B
                          ← populated by a follow-up PR
  runs/                  ← raw eval-run output (gitignored)
                          ← populated by the runner in a follow-up PR
```

## Scenario file shape

Every scenario is a YAML file in `scenarios/`. The fields are the same across A and B; some are populated only on one side.

```yaml
id: a1_mechanism            # short stable identifier — also the filename prefix
test: A                     # A (supplement) or B (alternative)
title: "Mechanism explanation"
what_it_tests: "One sentence on why this scenario exists."

# Sent to the chat as ?scenario_context= (Test A only). Mirrors what the
# app-v2 drawer would prepend when opening from a report.
scenario_context: |
  ...

# The user's actual message.
prompt: |
  ...

chat_settings:
  model_backend: uk_python  # uk_compiled | uk_python
  num_runs: 3               # how many fresh sessions to run per scenario

# Test A — qualitative grading rubric.
rubric:
  relevance: "1-5 — chat answers the actual follow-up"
  methodology: "1-5 — chat states dataset/year/assumptions"
  reasonableness: "1-5 — numbers in plausible range, internally consistent"
  consistency_with_report: "1-5 — agrees with report on shared facts"
  honesty: "1-5 — refuses cleanly vs fabricates"

# Test B — numeric comparison against a fixture.
reference:
  fixture: pe_api/b1_society_wide_pa.json
  fields_to_compare:
    - path: budget.budgetary_impact
      tolerance_pct: 1.0
    - path: decile.relative
      tolerance_pct: 1.0
```

The runner (added in a follow-up PR) reads every YAML in `scenarios/`, POSTs to a configured chat backend, and writes outputs under `runs/<timestamp>/<scenario-id>/`. See SPEC.md "Roadmap" for what's coming next.

## Decision thresholds

Pre-committed in SPEC.md so we don't rationalize ambiguous results later:

- **Test A** passes if mean rubric score ≥ 4.0 across all responses, no individual score < 2 on Reasonableness or Honesty, and ≤ 1-in-5 fabrication rate.
- **Test B** passes if field-level accuracy ≥ 95%, self-consistency SD < 0.5% of mean, methodology drift in ≤ 1 of 5 scenarios, failure rate < 10%.

## Adding scenarios

Add a YAML file. That's it — the runner picks it up automatically. Keep the `id` short and stable (it appears in filenames and reports).

When adding a Test B scenario, also add the reference fixture under `fixtures/pe_api/` and reference it via the relative path in the scenario YAML.
