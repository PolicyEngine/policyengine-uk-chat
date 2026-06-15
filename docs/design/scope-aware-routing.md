# Design note: scope-aware routing — pay for the background only when you need it

Status: **Draft for discussion** (no implementation yet)
Scope: `backend/routes/chatbot.py`, `backend/prompts.py`, `backend/scripts/build_reference.py`
Related: the opt-in topic gate (#109, salvaged from #95), the scope/refusal contract (#102), and the confirm-first proposal on #102.

> All `file:line` references are against `main` at the time of writing and will drift; treat them as pointers, not contracts.

---

## 1. The problem

Two recent pieces both decide *what kind of response a question deserves*, at different altitudes:

- **#102 (scope/refusal contract)** adds a `SCOPE_AND_REFUSAL` block to `SYSTEM_PROMPT`. The main model reads it and decides: answer in scope / decline off-topic / explain "not modelled" / partially answer with a caveat. This decision is **good and nuanced**, but it happens *inside the expensive call* — entangled with the full background.
- **#109 (topic gate)** runs a cheap Haiku classifier *before* the main loop and, for clearly off-topic input, returns a **canned string** without loading anything. This is **cheap**, but it's binary (on/off-topic) and the reply is a fixed template, not a response to the user's actual question.

The cost driver is the **background** the main call carries. In `_build_system_blocks` (`chatbot.py:135`) the `system` payload is `SYSTEM_PROMPT` (~1.8k tokens) **plus the whole `reference.md`** (`REFERENCE_DOC`, ~20k tokens), and the request also carries the six tool schemas (`_tool_defs_for_anthropic`, ~2.5k tokens). Today **every** message that reaches the loop pays for all of that — including the ones whose correct answer is "I can't model that" or "did you mean the fiscal impact?", which need none of it.

## 2. The key distinction (and the correction to "canned responses")

A call to the model has three inputs (`chatbot.py:483`):

```python
stream_kwargs = {
    "model":   model,            # Haiku unless the convo exceeds 120k tokens
    "system":  system_blocks,    # THE BACKGROUND: SYSTEM_PROMPT + REFERENCE_DOC (+ directives)
    "messages": conversation,    # the user's actual prompt + history
}
if not plan_mode:
    stream_kwargs["tools"] = tools   # the six tool schemas
```

Two things are separable:

- **The background** = `system` (+ `tools`). Big, mostly the reference doc. Expensive.
- **The response** = the model reading `messages` (the user's real prompt) and writing a reply.

The topic gate throws away **both** for off-topic input (no model call at all → fixed string). The better move is to keep the model **generating from the user's prompt** — so the reply is contextual — and drop only the **background it doesn't need**. The saving comes from *not loading the reference doc + tools*, not from replacing the model with a template.

**The codebase already does exactly this varying-by-mode.** In plan mode, `chatbot.py:490` simply omits `tools` from the request — same `system`, same `messages`, lighter call. Scope-aware routing generalises that: vary `system_blocks` *and* `tools` per request, while always passing `messages` through the model.

## 3. Proposed shape: a router that picks the background, not the words

Replace the binary gate with a cheap **scope router** (one fast-model call, or a rules+model hybrid) whose only job is to choose *which background tier* the real answer needs. Every tier still calls the model on the user's actual `messages`; they differ only in `system`/`tools`.

| Router verdict | `system` handed to the model | `tools` | Loads `reference.md`? |
|---|---|---|---|
| Off-topic | tiny "decline + redirect" prompt | none | **No** |
| In-scope, clearly **unmodelled** | small scope descriptor + "explain what's not modelled and what you can do" | none | **No** |
| **Partial** (modelled lever + unmodelled dimension) | small descriptor + "state the boundary, offer the modelled analysis" → **confirm-first** | none | **No** |
| Needs real computation | full `SYSTEM_PROMPT` + `REFERENCE_DOC` | all six | **Yes** |

Consequences:
- The 20k-token reference doc and the tool schemas load **only on the compute branch**. Off-topic, unmodelled, and confirm-first turns each cost one Haiku call on a tiny prompt.
- Every reply is **model-generated from the user's prompt** — no canned strings. An off-topic refusal can name the actual question; an unmodelled answer can be specific about *what* isn't modelled.
- This is the natural home for the **confirm-first** behaviour proposed on #102: the router classifies a question as "partial," the model states what it can/can't do (cheaply), and the expensive `run_economy_simulation` is deferred to the next turn, *after* the user agrees — so we never spend the engine on an answer the user didn't want.
- #109's gate becomes the degenerate first row (and a safe, shippable first step toward this).

## 4. The load-bearing piece: a distilled scope descriptor

The router can't carry the full `reference.md` (that would defeat the purpose), but the "unmodelled" and "partial" verdicts require *some* knowledge of what's modelled. The enabler is a **compact scope descriptor** — a few hundred tokens, e.g.:

```
Modelled: income tax, NI, Universal Credit, child benefit, pension credit, ...
Datasets: FRS, Enhanced FRS (efrs). Years: <from capabilities()>.
NOT modelled: macro/second-round effects (inflation, GDP, employment),
behavioural response, non-UK policy, unannounced/future Budgets, legal/filing advice.
```

This is ~1% of the reference doc and is enough for *coarse* routing. Its risks, and the mitigations the design depends on:

- **It's a lossy approximation.** It can't encode every parameter, so genuinely fine-grained "is X modelled?" questions can misroute. → **Fail safe toward the full model**: when unsure, route to the compute tier. A wrong "compute" just pays the background you'd have paid anyway; a wrong "unmodelled" tells a user something isn't modelled when it is — worse — so bias hard against it. (Same fail-open philosophy as the gate.)
- **It must not drift from the engine.** Hand-maintaining it invites exactly the inconsistency that #102's original `inflation` contradiction showed. → **Auto-derive it from `capabilities()`** at build time. `scripts/build_reference.py` already introspects the engine to stamp `reference.md`; it can emit a distilled scope summary as a byproduct, so the router and the in-prompt `SCOPE_AND_REFUSAL` share one source of truth.
- **Two layers must agree.** The router descriptor and `SCOPE_AND_REFUSAL` both describe scope; if they diverge, behaviour is inconsistent. Deriving both from the same `capabilities()` source keeps them aligned.

## 5. What does *not* move to the cheap pass

- Anything needing real numbers — the engine, obviously.
- Deep parameter-level scope questions whose answer truly depends on the full Parameters schema. The router does *coarse* triage; these fall through to the compute tier (full background or a `capabilities()`/introspection tool call). The router never replaces the main model's judgement on hard cases — it only spares it the easy ones.

## 6. Cost sketch

On warm traffic the prefix is cached (cache-read ≈ 0.1× input), so a non-compute turn that *still loads it* is ~0.26¢; the router avoids that, leaving ~one small Haiku call (~0.03¢) plus the model's short generated reply. On **cold/bursty** traffic (demos), a non-compute turn that loads the prefix pays a full cache-*write* of ~24k tokens (~3¢ on Haiku); the router avoids it entirely. The win scales with the share of traffic that resolves to decline/unmodelled/confirm, and with how cold the cache runs.

## 7. Phasing

1. **#109 as-is** — the binary gate, off-topic → canned. Already a safe, opt-in floor.
2. **Off-topic pass-through** — replace the canned string with a light-background model call (small `system`, no `tools`, user's prompt). Tailored refusals, still cheap.
3. **Distilled descriptor from `capabilities()`** — emit it from `build_reference.py`; wire it into the router prompt.
4. **Unmodelled + partial/confirm tiers** — full router; confirm-first lands here. Gate the whole thing behind an eval suite (the `personal-allowance → inflation` flow is the canonical case) so routing is enforced deterministically, not just prompted.

## 8. Open questions

- Router as a separate Haiku call, or fold the triage into the *first* turn of the main model with a minimal prompt that can escalate (emit a "needs compute" signal / call a `load_reference` tool) — i.e. progressive disclosure within one model rather than two?
- Where does the descriptor live and how is it cache-keyed so it doesn't bust the main prefix?
- How much of `SCOPE_AND_REFUSAL` (#102) collapses into the router once it exists, vs. stays as the compute-tier's in-prompt guidance?
- Latency: the router adds a round-trip to every message (incl. on-topic), same tradeoff as the gate — acceptable, or only for high-off-topic deployments?
