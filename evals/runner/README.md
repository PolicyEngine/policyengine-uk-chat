# eval runner

POSTs each scenario in `evals/scenarios/*.yaml` to a chat backend, saves raw SSE + extracted text + a meta JSON per run. No grading — that's a separate step.

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

- It is **not** a grader. Test A grading is a human step; Test B has a separate extractor PR.
- It is **not** parallel. Sequential by design for clean logs and to avoid hitting backend rate limits during long economy-wide runs. If/when we move to ~50 scenarios, add a `--parallel N` flag.
- It does **not** mutate the scenarios or fixtures dir.
