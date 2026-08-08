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

```bash
ANTHROPIC_API_KEY=... make eval-ai-live-uk-population
```

Runs the manual, data-backed UK population microsimulation tool-loop cases.
These cases execute the live model against local `policyengine.py` UK
microdata-backed simulations, run three trials per case, and pass only if the
configured pass-rate threshold is met.

Set `RUN_DATA_EVALS=1` to include cases that require local microdata.
Cases marked `requirements: [live_model]` are skipped offline and run only
through `make eval-ai-live`.

```bash
EVAL_BACKEND_URL=https://your-backend.example \
EVAL_RUN_TOKEN=... \
make eval-ai-deployed-uk-population
```

Runs the 20 population cases through the same deployed UK Chat model and tool
loop used by `/chat/message`. Each trial is a separate request to the
token-protected `/eval/chat/message` route, has a 600-second timeout, and is
graded first from the internal gateway trace and then from the complete tool
trace and answer returned by the backend. The population cases require
`compute/ready`, the current simulation-year default, reform confidence of at
least 80, and at least one validated parameter binding. This keeps routing and
reform-resolution failures separate from simulation, derivative-tool, and
answer failures. The runner uses four concurrent requests by default and does
not retry failed requests.

Use `python -m eval.run_deployed --case-id CASE_ID` to run one case. The token
is read only from `EVAL_RUN_TOKEN`; it is never accepted as a command-line
argument or written to reports. GitHub's `Run deployed UK population evals`
workflow provides the same runner manually and uploads reports even when a case
fails.

The backend must have a matching `UK_CHAT_EVAL_TOKEN`. Production and preview
deployments receive it from the repository's `UK_CHAT_EVAL_TOKEN` secret. The
endpoint returns 503 when that server secret is not configured and 401 for a
missing or invalid request token.

### Detached Modal batch

`modal_eval_app.py` submits one Modal function input per population case. All
20 inputs are submitted together with `spawn_map`; Modal may reuse any idle
worker container and may scale the worker pool to at most 25 containers. Each
case runs its three trials concurrently inside its worker. Run it detached so
the batch survives a terminal disconnect:

```bash
POLICYENGINE_UK_CHAT_EVAL_MODAL_APP_NAME=pe-uk-chat-evals-231 \
POLICYENGINE_UK_CHAT_EVAL_MODAL_SECRET_NAME=pe-uk-chat-231-secrets \
modal run --detach modal_eval_app.py \
  --backend-url https://policyengine--pe-uk-chat-231-web.modal.run
```

Each worker writes a distinct JSON report under the printed run ID in the
`policyengine-uk-chat-eval-reports` Modal Volume. After all 20 files exist,
download that run and combine it into the normal JSON and Markdown reports:

```bash
modal volume get policyengine-uk-chat-eval-reports RUN_ID /tmp/RUN_ID
PYTHONPATH=backend python -m eval.collect_modal \
  --case-report-dir /tmp/RUN_ID
```

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
