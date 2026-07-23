# Architecture

PolicyEngine UK Chat is a Next.js frontend and FastAPI backend. The backend uses
Anthropic for the chat loop and `policyengine.py` with `policyengine-uk` for all
tax-benefit calculations.

```text
Browser
  |
  | HTTP / SSE
  v
FastAPI API
  |
  +-- gateway: classify request and ground slots
  +-- chat: run the model/tool loop
  +-- tools: typed discovery -> validation -> simulation -> derivative -> artifact
  +-- engine: policyengine.py UK boundary and official output adapters
  +-- conversations / billing / observability
```

## Calculation flow

For a society-wide request, the chat loop creates one shared
`ToolExecutionContext`:

1. `run_society_simulation` resolves the configured dataset and runs baseline
   and reform `Simulation` objects in memory.
2. The result store keeps the pair and returns an opaque `simulation_id`.
3. A derivative tool retrieves the pair and invokes a policyengine.py output
   class, which applies survey weights.
4. The derivative result is stored under its own typed handle.
5. `generate_chart` accepts the matching handle and emits a deterministic chart
   spec.

This design prevents the model from receiving raw survey rows and prevents the
application from replacing weighted output logic with custom NumPy or pandas
aggregation.

## Backend packages

| Package | Responsibility |
| --- | --- |
| `api/` | FastAPI assembly, health/version endpoints, response handling. |
| `chat/` | Streaming model loop and system-block assembly. |
| `gateway/` | Lightweight routing and slot grounding. |
| `tools/` | Tool schemas, registry, dispatch, and turn-local result context. |
| `engine/` | policyengine.py runtime, discovery, validation, simulations, derivatives. |
| `eval/` | Offline/live eval runner and graders. |

## Engine versions

`GET /version` returns the policyengine.py package version and the installed UK
country package version. Dependency pins are shared by Docker and Modal through
`backend/requirements.txt`.
