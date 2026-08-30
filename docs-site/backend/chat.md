# The chat runtime

The chat runtime keeps the conversation primary and brings typed capabilities
into it only when needed. Calculation behavior is documented in
[Tools](tools.md).

## Conversation loop

`backend/chat/capability_service.py` supplies the complete supported message
history, current compatible artifact summaries, and public capability
descriptions to the conversation model. A model turn may return prose, one or
more capability calls, or both. Capability results are returned to the same
conversation loop until the model produces the user-facing response.

There is no global analysis phase and no alternate chat implementation selected
by configuration. Independent capability invocations may complete, request
clarification, report an unsupported request, or fail without forcing the whole
conversation into a shared lifecycle state.

## Required calculations

The model must invoke:

- `policy_information` for government-policy formulation, scope, formula, or
  calculation-method questions;
- `household_analysis` for a described household's tax, benefit, entitlement,
  or policy impact; and
- `society_analysis` for population-wide reform or benefit impacts.

Other supported questions may be answered directly. A private relevance
capability checks every turn but cannot choose another capability or construct
domain input.

## Typed execution

`InvocationExecutor` is the shared execution boundary for capabilities and
tools. It validates the registered caller permissions, Pydantic input and
output, prerequisite declarations, cancellation, parent invocation identity,
and trace state. Provider-specific model blocks remain in `chat/model_port.py`;
calculation, validation, aggregate derivation, and chart construction remain
behind typed tools.

## Retained conversational state

Capabilities persist immutable, versioned artifact summaries and validated
partial input. Consumers declare compatibility requirements instead of parsing
earlier assistant prose. Complete population simulation objects and record-level
arrays stay request-local; only aggregate results are durable.

## Response verification

Capabilities return validated facts without imposing a fixed prose layout. The
conversation model writes ordinary Markdown. When quantitative capability facts
are used, a private deterministic verifier checks sign, scale, currency,
percentages, and rounding. Household responses also enumerate every applied
material assumption under one `Assumptions used` heading.

## Streaming events

The public stream emits:

- `chunk` for user-facing response text;
- `invocation_activity` for sanitized capability/tool status updates;
- `suggestions` for optional follow-up prompts;
- `done` for the final response metadata; and
- `error` for a safe user-facing failure.

Normal activity includes public invocations without debug values. Debug mode
also includes private calls and expandable structured input/output projections.
The frontend stores activity separately from the model transcript.
