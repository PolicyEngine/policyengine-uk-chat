# eval runner + grader

Two scripts:

- `run.py` — POSTs each scenario in `evals/scenarios/*.yaml` to a chat backend, saves raw SSE + extracted text + meta JSON per run under `evals/runs/<timestamp>/`.
- `grade.py` — reads a finished run dir. For Test A scenarios, emits a markdown grading sheet the human fills in. For Test B, runs automated numeric extraction + anchor checks against fixtures.

## Run

```sh
# All scenarios, default backend (PR 51 preview).
python evals/runner/run.py

# Just some.
python evals/runner/run.py a1_mechanism b1_society_wide_pa

# Preview what would run.
python evals/runner/run.py --dry-run

# Point at a different backend.
python evals/runner/run.py --backend-url https://policyengine-uk-chat.vercel.app
# or
UK_CHAT_BACKEND_URL=https://... python evals/runner/run.py
```

## Vercel preview deployments

If the backend is a Vercel preview behind deployment protection, set the bypass token from the chat project's "Protection Bypass for Automation" setting:

```sh
UK_CHAT_BYPASS_TOKEN=... python evals/runner/run.py
```

The token is appended to the request URL as `?x-vercel-protection-bypass=...`. It's redacted in the saved meta JSON so the artifact is safe to share.

## Output

```
evals/runs/<timestamp>/
  manifest.json                          # one row per (scenario, run)
  <scenario_id>/
    scenario.yaml                        # frozen copy of what was run
    run-1.sse                            # raw SSE stream
    run-1.txt                            # concatenated chunk deltas (final answer)
    run-1.meta.json                      # event counts, timing, errors, redacted URL
    run-2.sse / run-2.txt / run-2.meta.json
    ...
```

`evals/runs/` is gitignored — these are artifacts of a specific run, not source.

## Dependencies

```sh
python -m venv .venv && source .venv/bin/activate
pip install -r evals/runner/requirements.txt
```

`httpx` for the streaming POST, `pyyaml` for scenario loading. Both stdlib-adjacent — no LLM frameworks, no Anthropic SDK on this side.

## What the runner is not

- It is **not** parallel. Sequential by design for clean logs and to avoid hitting backend rate limits during long economy-wide runs. If/when we move to ~50 scenarios, add a `--parallel N` flag.
- It does **not** mutate the scenarios or fixtures dir.

---

## Grade

```sh
# Generate A_grading.md (human sheet) + B_results.md (automated diffs).
python evals/runner/grade.py evals/runs/2026-05-15_120000

# Just one path.
python evals/runner/grade.py <run_dir> --test A
python evals/runner/grade.py <run_dir> --test B

# After A_grading.md has been filled in by a human, apply Test A thresholds.
python evals/runner/grade.py <run_dir> --threshold-check
```

### Test A flow

`grade.py --test A` walks the run dir and produces `A_grading.md`. Each A response gets a section with:

- Prompt and scenario_context (collapsible)
- The anchor (must_mention / must_not_say with regex hit/miss, plus `ideal_explanation`)
- The chat response
- Empty score fields for each rubric dimension

The grader (you) opens the file in an editor, replaces each ⬜ with a 1-5 score, and marks the fabrication question yes/no.

Then `--threshold-check` parses the filled sheet and applies the SPEC.md thresholds: mean rubric ≥ 4.0, no individual < 2 on reasonableness/honesty, fabrication rate ≤ 20%. Output goes to `threshold_check.md` and `.json`.

### Test B flow

Fully automated. For each B scenario:

- Loads the fixture from `evals/fixtures/pe_api/`.
- For each run, extracts numeric values from the response prose using per-field label regexes (heuristic).
- Diffs against the fixture with per-field `tolerance_pct`.
- Computes self-consistency (SD across runs as % of mean).
- Runs the anchor's `must_mention` / `must_not_say` regex checks.

Output: `B_results.json` (machine-readable) and `B_results.md` (human-readable per-scenario diffs + threshold verdict).

The extractor is best-effort regex over prose, so some fields legitimately come back as `⏭ no expected / couldn't extract`. Those are diagnostics for the grader, not failure verdicts.
