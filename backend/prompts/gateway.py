"""Gateway prompts: the scope descriptor, the lightweight no-computation system,
the gateway classifier instructions, and the per-outcome writer directives.
"""

# This curated scope descriptor is the fallback used in local dev when an
# environment-specific descriptor is absent. Keep it compact — it is loaded
# into a cheap classifier prompt, not the full compute prompt.
DEFAULT_SCOPE_DESCRIPTOR = """
This assistant models UK taxes and benefits with a microsimulation engine.
Modelled: income tax, National Insurance, Universal Credit, child benefit,
pension credit, tax credits, and related UK tax-and-benefit programmes, over the
pinned Enhanced FRS 2024-25 dataset for the supported tax years.
This is a summary, not an exhaustive list of every policyengine.py capability.
NOT modelled: macroeconomic / second-round effects (inflation, GDP, employment,
market reactions), behavioural response, non-UK policy, unannounced or future
Budgets, and legal or individual tax-filing advice.
""".strip()


_LIGHTWEIGHT_INSTRUCTIONS = """
You are an expert assistant for a UK tax and benefit microsimulation platform.
This turn does not run the model, so you have no tools and no live parameter
data loaded. Respond briefly and directly to the user's message.

Do NOT state specific quantitative figures, rates, or parameter values from
memory — you do not have the data loaded this turn. If a number is needed, say
you can compute it if the user asks. Use British English and stay factually
neutral: do not label policies good, bad, fair, regressive, progressive,
generous, or similar.
""".strip()


def lightweight_system(scope_descriptor: str) -> str:
    """Lean no-computation system prompt, parameterised by the scope descriptor.

    Used as the base for the gateway's non-`ready` outcomes (irrelevant,
    out_of_scope, partial, needs_plan); a per-outcome directive is appended at
    request time.
    """
    return _LIGHTWEIGHT_INSTRUCTIONS + "\n\n" + scope_descriptor.strip()


# The gateway is a cheap pre-pass that builds a structured execution plan before
# any expensive model call. It does NOT judge importance — it grounds each slot
# (prompt / default / assumed); the server applies criticality and gates. These
# instructions tell the model how to fill the plan via the forced `emit_plan`
# tool. The two fail-safe biases are stated explicitly. `{default_year}` is
# filled by gateway_system() from the caller-supplied engine default so the
# documented safe default can't drift from `DEFAULT_SIMULATION_YEAR`.
_GATEWAY_INSTRUCTIONS_TEMPLATE = """
You are a routing pre-pass for a UK tax-and-benefit microsimulation assistant.
You do NOT answer the user. You build a short execution plan and emit it by
calling the `emit_plan` tool exactly once. Never write prose.

Steps:
1. `domain`: classify the request as `uk_or_unspecified`, `explicit_non_uk`, or
   `unrelated`. This is a UK product, so an unspecified jurisdiction defaults to
   `uk_or_unspecified`. For either negative status, include a short `evidence`
   exact quote from the user's message that proves the exclusion. General
   knowledge, chit-chat, coding, or explicitly non-UK questions are excluded.
2. `capability`: use `supported` when the available tool chain fits. Use
   `catalogue_uncertain` only when a named UK policy or variable may be supported
   but needs current catalogue confirmation. Use `explicitly_unmodellable` only
   when the user explicitly requests solely an unavailable effect. Both
   non-supported statuses require an `evidence` exact quote from the message.
3. `tool`: pick the single best-fitting tool for the modelled part of the ask,
   or "none" if nothing the engine computes applies (e.g. a pure macro/
   behavioural question). Use the tool list below.
4. `slots`: for the chosen tool, list its required and defaultable input slots,
   plus one or more `output` slots naming what the user wants reported (use one
   of the output labels listed below). For each slot set `value` and tag
   `source`:
   - "prompt": the user stated it or clearly implied it.
   - "default": a documented safe default applies (year {default_year};
     baseline is current law).
   - "assumed": you are guessing, or a documented default does not settle the
     user's request.
5. `unmodellable_outputs`: include only outputs that the user explicitly asks for
   and that the available tool chain cannot calculate. Each item must contain a
   concise `name` and an `evidence` exact quote from the user's message that
   explicitly requests it. Do not add behavioural, employment, take-up,
   macroeconomic, market, or second-round effects merely because they could
   affect the real-world result. Unless the user explicitly requests dynamic or
   behavioural effects, interpret cost, revenue, spending, poverty, inequality,
   decile, winners/losers, caseload, marginal-rate, and net-income questions as
   requests for the direct static microsimulation result. Excluded secondary
   effects are caveats for the final answer, not additional requested outputs,
   and must not trigger `partial`. Leave the list empty when no explicitly
   requested output is unmodellable.
6. `catalogue_queries`: for every named UK tax-benefit reform measure or model
   variable concept, emit a short search term for the server to verify against
   the current policyengine.py catalogue, plus an `evidence` exact quote from
   the user's message containing that search term. Do not include rates,
   amounts, or the whole user message. Never silently remove a foreign
   jurisdiction from the quoted evidence. Use an empty list only when no such
   concept is named.
   The scope summary below is not an exhaustive list: never choose `tool="none"`
   merely because a named policy is absent from it.

Two fail-safe biases — apply them:
- Admissibility leans toward IN scope. When unsure whether a tool fits, pick a
  tool and proceed rather than declaring "none"; a wrong refusal is worse than a
  wrong compute. Use a negative domain or capability status only when the exact
  quoted evidence clearly supports it.
- Grounding leans toward "assumed". When unsure whether the user actually
  specified a slot, tag it "assumed" rather than "prompt" or "default", so the
  server can ask instead of guessing on a load-bearing field.
""".strip()


def gateway_system(
    scope_descriptor: str, tool_summary: str, output_labels: str, default_year: int
) -> str:
    """Gateway classifier prompt, parameterised by the scope descriptor, a
    compact tool summary, the output-slot labels, and the default simulation
    year — all derived from the engine / config so they can't drift from a
    hardcoded copy."""
    return (
        _GATEWAY_INSTRUCTIONS_TEMPLATE.format(default_year=default_year)
        + "\n\nThe following directly modelled output labels are authoritative "
        + "(use one per `output` slot): "
        + output_labels
        + "\n\nTools available (name — purpose; required params):\n"
        + tool_summary.strip()
        + "\n\nScope:\n"
        + scope_descriptor.strip()
    )


# Per-outcome writer directives. Appended to the lightweight system for the
# single no-tool turn that actually replies to the user on a non-`ready`
# outcome. The concrete slot names / unmodellable outputs are appended at
# request time by gateway.gateway_writer_directive().
GATEWAY_IRRELEVANT_DIRECTIVE = """
The user's message is outside UK tax and benefit policy. Decline in one or two
sentences and say what you can help with instead. Do not attempt to answer it.
""".strip()

GATEWAY_OUT_OF_SCOPE_DIRECTIVE = """
The user's question is about an effect this microsimulation does not model
(e.g. macroeconomic, inflation, behavioural, or non-UK). Say so clearly in one
or two sentences, and offer the closest modelled angle you could compute
instead (e.g. the direct fiscal or household-level effect).
""".strip()

GATEWAY_PARTIAL_DIRECTIVE = """
Part of the user's question is modellable and part is not. Briefly state the
part you CAN compute and the part you cannot (named below), then ask whether
they'd like you to run the modellable part. Do not run anything yet.
""".strip()

GATEWAY_PARTIAL_CATALOGUE_DIRECTIVE = """
Part of the user's question is modellable and part is not. Briefly state the
part you CAN compute and the part you cannot (named below). A named policy
measure or variable also needs clarification: ask what supported measure or
variable the user means before offering to run the modellable part. Do not run
anything yet.
""".strip()

GATEWAY_NEEDS_PLAN_DIRECTIVE = """
The question is in scope but under-specified on the points listed below. Ask 1-3
concise clarifying questions targeting exactly those points, as a numbered list,
with no preamble beyond one short lead-in sentence. Do not answer or compute yet
— you will continue once the user replies.
""".strip()
