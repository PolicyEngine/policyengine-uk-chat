# uk-chat evaluation spec

> Defines how we test uk-chat output to decide whether the chat works better as a **supplement** to app-v2 reports (follow-up questions reports can't answer) or as an **alternative** producing the same answers reports do. Two tests, two pre-committed thresholds.

Related external context:
- [policyengine-app-v2#1036](https://github.com/PolicyEngine/policyengine-app-v2/pull/1036) — report chat drawer (the supplement integration)
- [policyengine-uk-chat#51](https://github.com/PolicyEngine/policyengine-uk-chat/pull/51) — backend selector + `scenario_context` (chat changes that enable both positionings)
- [policyengine-api](https://github.com/PolicyEngine/policyengine-api) — produces the reference numbers used in Test B

## Problem statement

Decide between two product positionings of uk-chat: **supplement** (a follow-up affordance on app-v2 reports for questions reports can't answer) or **alternative** (chat as a primary way to get the same answers reports give, in addition to or instead of the report UI). The decision needs evidence, not opinions. This spec defines the evaluation that produces that evidence.

## Two tests, two positionings, two thresholds

### Test A — Chat as supplement

**Question:** Does the chat usefully extend reports for the long-tail of follow-up questions?

**Test setup:**
- 5 scenarios of `(report shown, follow-up question)` pairs.
- Each scenario run 3× in fresh sessions to capture variance.
- Grade per-rubric (below), no PE-API comparison needed (these are questions reports can't answer).

**Rubric (per response, scored 1-5):**
- **Relevance.** Did the chat answer the actual follow-up, or pivot to something unrelated?
- **Methodology disclosure.** Did the chat say what dataset/year/assumption it used?
- **Reasonableness.** Are the numbers in a plausible range? Do they internally agree (sum-to-totals, decile orderings, sign of effect)?
- **Consistency with report.** Where the chat references something the report already showed, do they agree?
- **Honesty about limits.** When the chat can't answer, does it say so cleanly, or fabricate?

**Decision threshold (pre-committed):**
- Mean rubric score ≥ 4.0 across all responses.
- No single response scoring < 2 on Reasonableness or Honesty (those are the trust-killer failures).
- No more than 1 in 5 responses where the chat fabricates a figure not derivable from a run.

### Test B — Chat as alternative

**Question:** Can the chat produce the same answers app-v2 reports do, with comparable trustworthiness?

**Test setup:**
- 5 scenarios chosen to match shapes app-v2 already answers (economy-wide reform, household calc, MTR, etc).
- For each: run app-v2 report (or equivalent PE-API call) → record reference numbers. Run chat 3× per scenario → record numeric output.
- Compare numerically per scenario field.

**Metrics:**
- **Field-level accuracy:** % of numeric fields within 1% of PE-API.
- **Self-consistency:** standard deviation of each numeric field across 3 chat runs as a % of mean. Should be < 0.5% for the chat to be considered deterministic enough.
- **Methodology drift:** count of runs where chat picks a different methodology (e.g. poverty BHC vs AHC, dataset year, decile definition) than PE-API.
- **Failure rate:** % of runs that fail to produce comparable numbers (timeouts, errors, LLM-thrash episodes).

**Decision threshold (pre-committed):**
- Field-level accuracy ≥ 95%.
- Self-consistency SD < 0.5% of mean for every numeric field.
- Methodology drift in ≤ 1 of 5 scenarios.
- Failure rate < 10%.

**If thresholds met:** alternative positioning is viable. **If not:** supplement-only is the right framing.

## Test scenarios

Each scenario is a tuple of `(scenario_id, question shape, what's being tested, fixtures)`. All UK 2025, baseline = current law unless stated. All chat runs use the `uk_python` backend pinned to `enhanced_frs_2023_24.h5` (the same dataset PE-API uses).

### Test A — supplement scenarios

These all assume the user has just viewed a specific app-v2 report and asks a follow-up.

**A1 — Mechanism explanation**
- **Report shown:** UK PA-raise reform 2025 (current £12,570 → £15,000), economy-wide.
- **Follow-up question:** "The report says the top decile gains less in % terms than the 8th decile — why? Walk me through the mechanism."
- **What it tests:** Can the chat reason about *why* a result looks the way it does, beyond just quoting the numbers?
- **Doesn't fit a report** because the report shows what, not why. This is the long-tail question shape supplements should serve.

**A2 — Subset breakdown not in the report**
- **Report shown:** UK PA-raise reform 2025, economy-wide.
- **Follow-up question:** "How does this reform affect single parents with two children specifically? Breakdown by income decile."
- **What it tests:** Can the chat slice the population in a way the canonical report doesn't, computing fresh from the model?
- **Stress test** because this combines applying a reform *and* filtering the population — two operations that are individually non-trivial for the LLM via the Python backend's API.

**A3 — Comparative scenario the user invented**
- **Report shown:** UK PA-raise reform 2025.
- **Follow-up question:** "What if we'd also raised the higher rate threshold from £50,270 to £55,000 alongside the PA raise — would that be more or less progressive?"
- **What it tests:** Multi-parameter ad-hoc reform comparison. Users couldn't construct this in app-v2 without building a new report.
- **High-value supplement** if it works because it lets users iterate without leaving the page.

**A4 — Out-of-scope question, polite refusal**
- **Report shown:** UK PA-raise reform 2025.
- **Follow-up question:** "How would this same reform affect inflation forecasts?"
- **What it tests:** Honest scope refusal vs fabrication. PolicyEngine doesn't model macro feedback effects; the chat should say so cleanly.
- **The "honesty under pressure" test.** Easy to fail by confidently making something up.

**A5 — Historical/factual question, no simulation needed**
- **Report shown:** UK PA-raise reform 2025.
- **Follow-up question:** "How has the UK personal allowance changed over the last 15 years? Just the figures."
- **What it tests:** Whether the chat handles factual-lookup questions (which need no simulation) without unnecessary tool use, and whether it knows where the data lives.
- **Edge case** because this is information the underlying packages have but the chat may not surface cleanly — could waste tool calls trying to "calculate" what's just a parameter lookup.

### Test B — alternative scenarios

These are questions app-v2 already answers via reports. The chat must match.

**B1 — Society-wide reform, single-parameter**
- **Question:** "Run a UK economy-wide comparison for 2025: baseline current law, reform raises the income tax personal allowance from £12,570 to £15,000. Report total budgetary impact, decile income changes (both £ and %), and BHC poverty rates for all/child/working-age/senior."
- **PE-API reference:** Generated against the live PE-API by the fixture-build step and saved to `evals/fixtures/pe_api/b1_society_wide_pa.json`.
- **What it tests:** Baseline replication. If the chat can't match here, it can't match anywhere.

**B2 — Stacked NI + IT + threshold-freeze reform (Reeves 2025 pre-Budget package)**
- **Question:** Three changes stacked — extend the IT threshold freeze to 2029-30, reduce NI main rate 8%→6%, increase IT basic 20%→22% and higher 40%→42%. Report combined and per-layer revenue, per-reform decile impacts, and the example household (£60k earner + £10k pension) figures.
- **Reference:** PolicyEngine's published analysis by Vahid Ahmadi (Nov 2025) — `app/src/data/posts/articles/uk-income-tax-ni-reforms-2025.md` — gives canonical per-layer figures (£3.5bn freeze, £11.7bn NI cut, £18.6bn IT increase, £6.9bn combined in 2026-27) and per-decile percentages.
- **What it tests:** Multi-parameter reform with stacking. Does the chat understand and apply the stacking methodology correctly, and does it reproduce PolicyEngine's *own published* numbers?

**B3 — Household calc (no microdata needed)**
- **Question:** "Single adult, age 35, employment income £45,000 in UK 2025, no dependents, England. Compute: net income, income tax, employee NI, marginal tax rate at this income point."
- **PE-API reference:** to be generated via `/uk/household` endpoint.
- **What it tests:** Deterministic household calculations — single-household rule application without microdata or aggregation. The chat should be at its best here.

**B4 — MTR schedule**
- **Question:** "Compute the combined IT+NI marginal rate at gross income levels £10k, £20k, £30k, £50k, £75k, £100k, £125k, £150k for a single UK adult in 2025."
- **Ground truth:** Generated by directly calling `policyengine_uk` at fixture-build time. No microdata or PE-API involved.
- **What it tests:** Schedule lookup against rule-driven ground truth. Acts as a sanity check on the test infrastructure — if this scenario fails, the runner or extractor is broken before we draw conclusions about anything else.

**B5 — Remove the two-child benefit limit (Autumn Budget 2025)**
- **Question:** UK 2026-27 economy-wide — remove the two-child limit on UC and CTC. Report cost, decile impacts, child-poverty change, Gini change, illustrative household.
- **Reference:** PolicyEngine's published analysis by Vahid Ahmadi (Oct 2025) — `app/src/data/posts/articles/uk-two-child-limit.md` — gives £2.9bn cost in 2026-27, -13.5% absolute child poverty BHC, -0.55% Gini, D2 sees the largest relative gain.
- **What it tests:** Benefit-side reform (UC/CTC), not tax — different model surface from the other B scenarios. Tests reproduction of canonical PolicyEngine numbers on a recent published analysis.

## Anchors — golden-path guidance per scenario

Every scenario YAML has an `anchor` block that captures what an *ideal* response would look like — drawn either from PolicyEngine's own published research blog (for the scenarios we have one for) or from UK tax-rule knowledge. The anchor has three parts:

- **`must_mention`** — phrases or facts a good answer must include (regex-matchable).
- **`must_not_say`** — claims that would be wrong or misleading (also regex-matchable).
- **`ideal_explanation`** / **`ideal_finding`** — a prose sketch of what a Vahid-quality answer would look like, for human-grader reference.

In v1, anchors are **grader aids** — the human grader reads them before scoring so the rubric is calibrated to PolicyEngine's house standard. In v2 they become *inputs* to an LLM-judge that scores automatically.

Where anchors come from:

| Scenario | Anchor source |
|---|---|
| A1 — Mechanism | UK PA-taper rule + `uk-income-tax-ni-reforms-2025.md` discussion of the same dynamic |
| A2 — Subset slice | UK rule knowledge + reasoning about how PA changes affect single parents |
| A3 — Multi-param what-if | `uk-income-tax-ni-reforms-2025.md` stacking methodology + standard progressivity definitions |
| A4 — Out-of-scope | PolicyEngine scope (microsim, no GE/macro) |
| A5 — Historical lookup | UK PA parameter history |
| B1 — Society-wide PA | PE-API output (the fixture) + UK reporting norms |
| B2 — Stacked NI/IT/freeze | `uk-income-tax-ni-reforms-2025.md` published numbers |
| B3 — Household calc | UK tax rules at the £45k income point |
| B4 — MTR schedule | UK tax rule schedule (deterministic) |
| B5 — Two-child limit | `uk-two-child-limit.md` published numbers |

If we add scenarios later, the strong preference is to **anchor each one against a published PolicyEngine post** when there's one that matches. This makes the eval into "does the chat reproduce PolicyEngine's published analyses?" which is a much stronger framing than "does the chat match a one-off API call." It also makes the eval defensible externally — every reference number has a paper trail.

## Proposed solutions & tradeoffs

### Approach 1: hand-authored small scenarios, manual grading

**Description:** As above — 10 scenarios total (5 A + 5 B), 3 runs each = 30 conversations. Grader is a human (initially Sakshi).
**Pros:** Fast to set up. Grading captures qualitative judgment (methodology drift, hallucination) that automated metrics miss. Small enough that mistakes are recoverable.
**Cons:** N=5 per test is statistically thin. Manual grading is the bottleneck. Doesn't scale to ongoing monitoring.
**Verdict:** accepted for v1.

### Approach 2: larger automated test set

**Description:** ~50 scenarios per test, parsed numerics extracted via regex/LLM, compared automatically.
**Pros:** Better statistical power. Re-runnable on every chat change.
**Cons:** Significant up-front cost to build the harness and reliable extractors. Probably premature — we don't know yet what the failure modes are.
**Verdict:** deferred. Revisit after v1 results.

### Approach 3: live A/B with real users

**Description:** Ship both supplement and standalone-chat to a fraction of users, instrument usage, decide based on real behaviour.
**Pros:** Most ecologically valid signal.
**Cons:** Slow to gather data, exposes possibly-bad output to users, hard to attribute outcomes cleanly.
**Verdict:** rejected for the initial decision. Could be a follow-up after v1 confirms the basic story.

## Roadmap

This PR lands the spec, the 10 anchored scenarios, and the runner. Still to come (in this same PR):

1. **B fixtures.** Generate reference outputs for B1-B5 into `evals/fixtures/pe_api/` — PE-API calls for the society-wide scenarios, direct `policyengine_uk` calls for the household / MTR scenarios.
2. **Grader.** For Test A: a markdown grading sheet (one row per response) with the rubric and anchor columns. Manual fill for v1. For Test B: an extractor that pulls numerics from SSE responses and diffs against fixtures.
3. **Findings writeup.** Once 30 conversations are graded, a `RESULTS-YYYY-MM-DD.md` report. Per-scenario verdicts, threshold check results, recommended positioning, new failure modes if any.

The chat backend URL the runner targets is configurable via env var so the eval can run against either a preview deploy or production.
