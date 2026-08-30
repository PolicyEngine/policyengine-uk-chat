# Architecture

PolicyEngine UK Chat is a Next.js frontend and FastAPI backend. The backend uses
Anthropic for conversation and `policyengine.py` with `policyengine-uk` for all
tax-benefit calculations.

```text
Browser
  |
  | HTTP / SSE
  v
FastAPI API
  |
  +-- chat: retain full history and coordinate model turns
  +-- capabilities: resolve conversational requests and prerequisites
  +-- tools: validate typed input and run scoped deterministic operations
  +-- engine: policyengine.py UK calculations and official output adapters
  +-- persistence: conversation artifacts, partial input, traces, and receipts
  +-- conversations / billing / observability
```

## Runtime relationships

`ChatTurnService` supplies the complete conversation history to the model. The
model may answer directly or invoke one or more public capabilities. Each
capability coordinates typed tools and declared prerequisite capabilities. The
`InvocationExecutor` validates caller permissions, typed input and output,
dependencies, cancellation, and trace status for every registered call.

```text
ChatTurnService
  -> ConversationModel
  -> CapabilityRegistry
  -> InvocationExecutor
       -> Capability[Input, Output]
       -> Tool[Input, Output]
       -> InvocationTracer
  -> CapabilityContext
       -> conversation artifact repositories
       -> request-local TurnResultStore
```

Government-policy calculation methods, household amounts or impacts, and
population-wide impacts must use their corresponding capability. Other
supported conversation can remain a direct model response.

## Calculation flow

For a population-wide request, `society_analysis` resolves and validates a
policy scenario, runs the fixed-dataset simulation, and calculates the required
default aggregates plus supported user-requested aggregates. Complete simulation
objects stay in the request-local result store. Durable artifacts contain only
validated aggregates and compatibility metadata, so the model never receives
record-level survey rows.

## Backend packages

| Package | Responsibility |
| --- | --- |
| `api/` | FastAPI assembly, health/version endpoints, response handling. |
| `chat/` | Full-history model loop, public stream, narration verification. |
| `capabilities/` | Typed capability contracts, implementations, composition, execution, and tracing. |
| `tools/` | Typed tool contracts, registry, dispatch, and request-local result context. |
| `engine/` | policyengine.py runtime, discovery, validation, simulations, derivatives. |
| `persistence/` | SQL repositories for artifacts, partial input, traces, and idempotency. |
| `eval/` | Offline, live-model, and deployed-runtime evaluation adapters. |

## Engine versions

`GET /version` returns the policyengine.py package version and the installed UK
country package version. Dependency pins are shared by Docker and Modal through
`backend/requirements.txt`.
