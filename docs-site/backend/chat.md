# The chat agent

The chat agent answers quantitative questions through typed tools rather than
recalling numbers or writing executable code. Tool details are documented in
[Tools](tools.md); request routing is documented in [The gateway](gateway.md).

## Agent loop

`backend/chat/orchestrator.py` runs the gateway, selects a model, builds the
system blocks, and enters the streaming tool-use loop. One
`ToolExecutionContext` is created for the turn and shared by every tool call, so
simulation and derivative handles remain usable across iterations.

Tool calls within an iteration are dispatched concurrently. Results stream back
as they finish, while the Anthropic transcript preserves the original tool-call
order. The loop stops on a final answer or at the configured iteration cap.

## Model selection

`backend/chat/model_selection.py` uses the gateway plan, reform/distributional
signals, charts mode, and estimated input size to choose the fast, complex, or
reasoning model. Model IDs are configured in `backend/config/models.py`.

## Prompt rules

The compute prompt in `backend/prompts/system.py` requires the agent to:

- use discovery tools instead of guessing variable or parameter names;
- validate reforms and synthetic households before relying on them;
- use `run_axes_simulation` followed by `get_axes_series` for numeric household
  ranges, and pass that compact series to `generate_chart` when charting it;
- run `run_society_simulation` before requesting society derivatives;
- use the official budget, programme, decile, winners/losers, poverty,
  inequality, and aggregate tools;
- never expose row-level survey data or describe a synthetic household as real;
- pass derivative handles to deterministic preset charts; and
- keep explanations neutral and use British English.

There is no `run_python` fallback. Unsupported calculations must be stated as
unsupported or decomposed into existing typed tools.

## System blocks

`backend/chat/system_blocks.py` emits a cached compute prompt followed by
per-turn charts-mode and gateway-plan blocks. Lightweight gateway outcomes use a
separate no-tools prompt plus an outcome-specific writer directive.

## Streaming events

The backend streams text chunks, tool start/result events, suggestions, and a
final `done` event containing stop reason, usage, cost, balance, model, and
gateway metadata. The frontend renders chart code blocks emitted by
`generate_chart` as chart components.
