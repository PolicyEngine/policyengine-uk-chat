# Design doc: conversation memory layer — persist households/scenarios across turns

Status: **Draft for discussion** (issue [#86](https://github.com/PolicyEngine/policyengine-uk-chat/issues/86), tagged `needs-design`)
Author: design discussion — no implementation yet
Scope: backend (`backend/chat/`, `backend/tools/`, `backend/conversations/`, `backend/gateway/`) + frontend (`frontend/src/app/ChatPage.tsx`)

> This is a design doc to agree the shape before any code. All `file:line` references are against `main` (post-#112 gateway + post-#116 backend repackaging).

> **Refresh note (re-grounded after #112 and #116).** The original draft was written against the pre-`#116` layout (`backend/routes/chatbot.py`, `backend/agent_tools.py`, `backend/routes/conversations.py`) and before the AI gateway (#112) landed. Three things changed the design, not just the file paths:
> 1. **The backend was repackaged (#116).** The monolithic `routes/chatbot.py` is now the `chat/` package (`orchestrator.py`, `system_blocks.py`, `schemas.py`); tools are the `tools/` package (`definitions.py`, `dispatch.py`); conversations are the `conversations/` package; system prompts are the `prompts/` package. All refs below point at the new tree.
> 2. **The gateway landed (#112).** A cheap pre-pass (`backend/gateway/`) now grounds a per-turn *plan* (tool + slot values) before the heavy model runs. This **overlaps directly** with the scenario this doc proposes — see the new [§3.6](#36-reconciliation-with-the-gateway-112). It is the single biggest design input since the first draft.
> 3. **Typed tools landed.** `calculate_household` and `run_economy_simulation` are now registered in `TOOL_DEFINITIONS` (`backend/tools/definitions.py:214`, `:225`), so the "typed tools exist but aren't registered" caveat in the original Q4 is obsolete; the scenario schema should track those live signatures now. **Plan mode was removed** — its job is subsumed by the gateway's `needs_plan` outcome, so the old "conflict with Plan mode" question is recast in [Q3](#5-open-questions-issue-items--recommendations).

---

## 1. Problem & motivation

Today every chat turn is effectively one-shot. The agent has no structured memory of the household or reform it analysed on the previous turn — the only "memory" is the raw message transcript, which the model re-reads each turn.

Concretely, the streaming loop `stream_chat` (`backend/chat/orchestrator.py:47`) rebuilds the request from `chat_request.messages` (`ChatRequest`, `backend/chat/schemas.py:13`) every turn, prepends the cached system prompt via `_build_system_blocks` (`backend/chat/system_blocks.py:58`), and the agent answers quantitative questions by writing fresh Python in `run_python` (`backend/tools/dispatch.py:291`; declared in `TOOL_DEFINITIONS` at `backend/tools/definitions.py:250`). When a user says:

> "Single earner, £45,000, in Scotland — what's the marginal rate at £60k?"

…then follows up with:

> "What if they were married?"

…the agent must **re-derive the entire household** from scrollback to mutate one field. This is lossy: details silently drift (the country gets dropped, the income changes, the child count resets), the model re-pays tokens to reconstruct context, and comparative conversations — arguably the killer feature of a tax/benefit chatbot — feel brittle.

There is now a second consumer of that lost context: the **gateway** (`run_gateway`, `backend/gateway/runtime.py:146`) only ever sees the *last user message* (`backend/chat/orchestrator.py:133`). On the follow-up turn it sees just "what if they were married?" — so income, region, year and child count come back as `assumed`/missing slots, and the gateway either re-asks for them (`needs_plan`) or routes on a half-grounded plan. The same missing memory that hurts the writer now also degrades routing. See [§3.6](#36-reconciliation-with-the-gateway-112).

The proposal: persist a structured **active scenario** (household + reform + comparison baseline) per conversation. The agent reads it at the top of each turn and writes patches to it as the user introduces or modifies parameters, instead of re-extracting everything from text — and the gateway is seeded from it so it stops re-asking for inherited values.

---

## 2. Goals / non-goals

### Goals
- Persist a single structured `active_scenario` per conversation, surviving across turns, reloads, and shared links.
- Give the agent two tools: `get_scenario()` (read) and `update_scenario(patch)` (shallow-merge mutate).
- Instruct the agent (via the system prompt) to read the scenario at turn start and patch it whenever the user introduces/modifies household or reform parameters.
- Round-trip the scenario through the existing conversation save/load path (`backend/conversations/`) so it is durable and shareable.
- Show a subtle "active scenario" pill in the input chrome of `ChatPage.tsx`, with clear/reset.
- **Seed the gateway from the scenario** so inherited slots count as grounded and the gateway stops re-asking on follow-ups ([§3.6](#36-reconciliation-with-the-gateway-112)).
- Compose cleanly with the now-live typed tools (`calculate_household`, `run_economy_simulation`) so a persisted scenario can be fed straight into a typed call.

### Non-goals (for v1)
- **Not** building the typed `household`/`reform` execution tools — those have already landed. This layer *persists* structured args; it does not replace `run_python`.
- **Not** multi-scenario / named-scenario management (A vs B vs C side by side). One active scenario per conversation; forking is a future extension.
- **Not** server-authoritative scenario derivation from microsim *outputs*. The agent (and the gateway plan) own the scenario contents; the server stores and relays them. We do not parse the household back out of computed results.
- **Not** auto-running simulations when the scenario changes. `update_scenario` records intent; the agent still calls a compute tool.
- **Not** cross-conversation memory or per-user profiles.

---

## 3. Proposed design

### 3.1 The `active_scenario` schema

A free-ish JSON object the agent reads and patches. We keep it deliberately loose for v1 (the agent, not a Pydantic model, is the source of truth), but document a canonical shape so the prompt, the gateway seed, and the pill renderer agree:

```jsonc
{
  "household": {
    "earners": [
      { "employment_income": 45000, "marital_status": "single" }
    ],
    "children": 0,
    "country": "Scotland",   // England | Scotland | Wales | Northern Ireland
    "year": 2025
  },
  "reform": null,            // null = current law, else a reform dict (see below)
  "comparison_baseline": "current_law",
  "notes": null              // free-text the agent may use for nuance the schema can't hold
}
```

The `reform` field intentionally mirrors the structure the compute path already accepts — `build_compiled_policy` (aliased `_build_compiled_policy`, `backend/tools/dispatch.py:35`, used at `:92`/`:119`) keys reforms by programme (`income_tax`, `national_insurance`, `universal_credit`, …). That means a persisted `reform` can be handed straight to the typed `run_economy_simulation` / `calculate_household` tools (`backend/tools/definitions.py:225`, `:214`) or splatted into a `Parameters(...)` call inside `run_python`, with no translation. Typed args make the scenario *expressible*; this issue makes them *durable*.

Design choice: the schema is **advisory, not validated** server-side in v1. The server stores whatever the agent writes (after a size cap + JSON-shape check). Reasoning in §7.

### 3.2 New tools: `get_scenario` / `update_scenario`

Tools are defined as plain functions and dispatched through the dispatch dict inside `execute_tool` (`backend/tools/dispatch.py:301`, `:306`); they are declared to Anthropic in the `TOOL_DEFINITIONS` list (`backend/tools/definitions.py:202`). Adding a tool today means: write the function, add it to the dispatch dict, add a schema entry to `TOOL_DEFINITIONS`.

**The wrinkle:** every existing tool in that dispatch dict is *stateless* — `run_python`, `generate_chart`, and the typed sims take only their declared inputs and touch no conversation state. The scenario tools are different: they read/write **per-conversation state**, which `execute_tool` has no handle on today. `execute_tool` is called from the streaming loop via a thread executor with just `(name, input)`:

```python
# backend/chat/orchestrator.py:310 (inside execute_tool_async, :307)
result = await loop.run_in_executor(None, execute_tool, tu["name"], tu["input"])
```

So we cannot implement these as pure functions in the existing dispatch dict without threading state through. Two viable approaches:

**Option A — handle the scenario tools in the chat loop, not in `execute_tool`.**
Keep `get_scenario`/`update_scenario` in `TOOL_DEFINITIONS` (so the model sees them and the cache-stamping helper `_tool_defs_for_anthropic` at `backend/chat/system_blocks.py:39` forwards them), but intercept them in the loop *before* the `execute_tool` dispatch. The loop already owns a per-request mutable `conversation` list and gathers `tool_uses` (`backend/chat/orchestrator.py:160`, dispatched at `:298`–`:338`); we add a sibling `scenario` dict in the same scope. When a `tool_use` block names `update_scenario`, the loop shallow-merges the patch into `scenario` and returns the new state as the tool_result; `get_scenario` returns the current `scenario`. This keeps `execute_tool` stateless and is the **recommended** approach.

  Sketch (in the tool-dispatch region around `backend/chat/orchestrator.py:298`–`338`):
  ```python
  SCENARIO_TOOLS = {"get_scenario", "update_scenario"}
  # when building each tool_result, split scenario tools out of the executor path:
  if tu["name"] == "update_scenario":
      scenario = _merge_scenario(scenario, tu["input"].get("patch", {}))
      result = {"status": "ok", "active_scenario": scenario}
  elif tu["name"] == "get_scenario":
      result = {"active_scenario": scenario}
  else:
      result = await loop.run_in_executor(None, execute_tool, tu["name"], tu["input"])
  ```
  `_merge_scenario` lives in `backend/tools/` (pure, unit-testable): shallow-merge top-level keys, with `household` merged one level deeper so `{"household": {"earners": [...]}}` doesn't clobber `country`/`children`. `patch == {"reform": null}` explicitly clears the reform.

**Option B — pass a mutable context into `execute_tool`.**
Change `execute_tool(name, input, context=None)` and register stateful tools that read/write `context["scenario"]`. More uniform, but it touches the signature every tool and every test in `backend/tests/` depends on, plus the executor call site. Heavier; not recommended for v1.

> **Tools exist only on the `ready` route.** The gateway routes non-`ready` outcomes (`irrelevant` / `out_of_scope` / `partial` / `needs_plan`) to a lightweight, **no-tool** reply turn (`_build_lightweight_system_blocks`, `backend/chat/system_blocks.py:87`; `route="lightweight"`), so `update_scenario` can only be called on `ready`/compute turns. This is the structural successor to the old Plan-mode tool omission — see [Q3](#5-open-questions-issue-items--recommendations).

> Note on prompt caching: `_tool_defs_for_anthropic` (`backend/chat/system_blocks.py:39`–`:50`) stamps `cache_control` on the **last** tool only, so the whole tool array caches as one block. Adding two tools to the end of `TOOL_DEFINITIONS` is fine — the cache breakpoint just moves to the new last tool. Keep the additions *stable in order* to avoid needless cache invalidation. (The gateway's `emit_plan` tool is deliberately *not* in `TOOL_DEFINITIONS` — `backend/gateway/runtime.py:75` — so it never enters this array.)

**`TOOL_DEFINITIONS` entries** (added to the list at `backend/tools/definitions.py:202`):

```python
{
  "name": "get_scenario",
  "description": "Return the conversation's persisted active scenario "
                 "(household, reform, comparison_baseline). Call this at the "
                 "start of a turn to recover context the user built earlier "
                 "instead of re-reading the transcript.",
  "input_schema": {"type": "object", "properties": {}},
},
{
  "name": "update_scenario",
  "description": "Shallow-merge a patch into the active scenario. Call this "
                 "whenever the user introduces or modifies household or reform "
                 "parameters (income, region, marital status, children, year, "
                 "reform, comparison baseline). Pass only the changed fields. "
                 "Set a field to null to clear it (e.g. {\"reform\": null}).",
  "input_schema": {
    "type": "object",
    "properties": {
      "patch": {
        "type": "object",
        "description": "Partial active_scenario. Top-level keys: household, "
                       "reform, comparison_baseline, notes. household is merged "
                       "one level deep.",
      }
    },
    "required": ["patch"],
  },
},
```

### 3.3 System prompt instructions

The system prompt is assembled by `_build_system_blocks` (`backend/chat/system_blocks.py:58`): a cached `SYSTEM_PROMPT` block (`backend/prompts/system.py:163`, assembled from `SYSTEM_PROMPT_SECTIONS` at `:149`), a cached reference-doc block, then optional **per-turn** directives appended *after* both cache breakpoints — Charts mode (`backend/chat/system_blocks.py:80`–`81`) and the **gateway plan** (`:82`–`83`, fed by `serialise_plan_for_system`) — so toggling them never invalidates the cache.

We add scenario guidance in two parts:

1. **Static behavioural rules** → appended to `SYSTEM_PROMPT_SECTIONS` (`backend/prompts/system.py:149`), inside the cached block. Roughly:
   > CONVERSATION SCENARIO MEMORY:
   > - A structured `active_scenario` persists across turns. At the start of a turn that touches a household or reform, call `get_scenario()` to recover what the user already specified rather than re-deriving it from the transcript.
   > - Whenever the user introduces or changes any household/reform parameter, call `update_scenario(patch)` with only the changed fields, then compute.
   > - If a change is ambiguous (e.g. "make them richer"), ask a brief clarifying question before patching — do not invent values.
   > - The scenario is advisory context for *you*; every number in your answer must still come from a compute tool, not from the scenario.

2. **Live scenario snapshot** → injected as a **per-turn** block in `_build_system_blocks`, after the cache breakpoints, **composed with the existing gateway-plan block** rather than added as a third parallel block (see [§3.6](#36-reconciliation-with-the-gateway-112)). `_build_system_blocks` gains a `scenario: dict | None = None` parameter, and the orchestrator passes the loaded scenario alongside `gateway_plan` (around `backend/chat/orchestrator.py:143`–`146`):
   ```python
   if scenario:
       blocks.append({"type": "text",
                      "text": "ACTIVE SCENARIO (current persisted state):\n"
                              + json.dumps(scenario, indent=2)})
   ```
   Injecting the snapshot here (rather than relying solely on `get_scenario()`) means the agent sees the state for free without spending a tool round-trip, while still being able to call `get_scenario()` for the canonical copy. Because it's after the cache breakpoint, a changing scenario never busts the cached prompt/reference. This mirrors the existing per-turn-directive pattern precisely.

### 3.4 Server-side persistence

Conversations persist through `backend/conversations/`: a SQLModel `ChatConversation` table (`backend/conversations/models.py:13`) backed by `DATABASE_URL` (Postgres — in production the Supabase Postgres instance; distinct from the `supabase` client used only for billing under `backend/billing/`). Messages are stored as a JSON string in the `messages` column (`backend/conversations/models.py:18`); save is upsert-by-`session_id` in `save_conversation` (`backend/conversations/store.py:20`; update branch `:31`, insert branch `:43`); load is by id in `get_conversation` (`backend/conversations/store.py:80`); shared load is `get_shared_conversation` (`backend/conversations/sharing.py:33`).

We add one nullable column, `active_scenario` (TEXT, JSON-encoded), to `ChatConversation`. The migration pattern already exists: `ensure_table` (`backend/conversations/models.py:39`) idempotently `ALTER TABLE ... ADD COLUMN` for new columns (`:48`). We extend that loop with `("active_scenario", "TEXT")`.

Then:
- `SaveConversationRequest` (`backend/conversations/schemas.py:6`) gains `active_scenario: dict | None = None`; `save_conversation` writes `json.dumps(...)` into the column on both the update and insert branches.
- `ConversationDetail` (`backend/conversations/schemas.py:22`) gains `active_scenario`; `get_conversation` and `get_shared_conversation` deserialize and return it.

**Who owns the canonical scenario at rest?** The chat loop holds the live scenario for the duration of a streamed turn (§3.2). At end of turn it must be persisted. Two paths, picked deliberately (open question Q1):

- The streamed `done` event (`backend/chat/orchestrator.py:264`; the iteration-cap path emits its own `done` at `:404`) already carries per-turn metadata; we add `active_scenario` to it. The frontend stores it in React state and includes it in the next `saveConversation` POST. This keeps the server stateless between requests, consistent with how `messages` already flow: the client owns the transcript and resends it each turn (`ChatRequest.messages`, `backend/chat/schemas.py:13`; `frontend/src/app/ChatPage.tsx:648`). **Recommended.**
- Alternatively, the chat route writes the scenario straight to the DB by `session_id`. This makes the server authoritative but introduces a second writer racing the client's `saveConversation` upsert. Avoid for v1.

Round-trip: client sends `active_scenario` (if any) in the chat request → loop seeds the live scenario from it → agent (and/or gateway, §3.6) patches it → `done` returns the final scenario → client stores it and POSTs it to `/conversations` alongside `messages`. On reload, `get_conversation` returns it; the client re-seeds it on the next chat request. This requires adding `active_scenario` to `ChatRequest` (`backend/chat/schemas.py:13`) too.

### 3.5 Frontend pill UI

`ChatPage.tsx` already has the right scaffolding:
- Per-conversation client state via `useState`, e.g. `messages`, `chartsMode` (`frontend/src/app/ChatPage.tsx:282`). (Note: `planMode` was removed alongside backend Plan mode, so the pill row now holds Charts only — the natural neighbour for an "Active scenario" pill.)
- The toggle/button row under the textarea (the Charts pill lives around `frontend/src/app/ChatPage.tsx:1821`–`1839`).
- Save/load already structured: `saveConversation` (`:473`), `loadConversation` (`:422`) hydrating from `ConversationDetail`, and the chat request body built at `:648`.

Changes (line numbers approximate — `ChatPage.tsx` drifts):
- New state: `const [activeScenario, setActiveScenario] = useState<ActiveScenario | null>(null);` next to `chartsMode` (`:282`).
- Read the scenario off the stream `done` event in the SSE handler (`:710`, where `data.session_id` is already consumed) → `setActiveScenario(data.active_scenario)`.
- Send it on the next request: add `active_scenario: activeScenario` to the body at `:648`.
- Persist it: add `active_scenario: activeScenario` to the `saveConversation` body (`:473` region); hydrate it in `loadConversation` (`:422`); clear it in the "new chat" handler (`setMessages([])` at `:546`).
- Render a pill in the toggle row (`:1821`), styled like the Charts pill, showing a **one-line summary** (e.g. "Single earner, £45k, Scotland") with an `×` to clear → `setActiveScenario(null)`. Clicking the pill body opens a small modal showing the full JSON structure with a "Reset scenario" button.
- A `summariseScenario(scenario)` helper produces the pill's one-liner from the canonical shape in §3.1.

### 3.6 Reconciliation with the gateway (#112)

This is the section the refresh adds, because the gateway changes the design rather than just sitting beside it.

**What the gateway already does.** Before the heavy model runs, `run_gateway(last_user_message)` (`backend/gateway/runtime.py:146`) makes one cheap forced-tool call (`emit_plan`, `:77`) that fills a structured plan: `in_domain`, a best-fit `tool`, and a list of **slots** — `SlotFact(name, kind, value, source)` where `source ∈ {prompt, default, assumed}` (`backend/gateway/policy.py`). The pure `gate()` then maps that plan to one of five outcomes. A slot *gates* (forces a clarifying question, `needs_plan`) iff its `source` is `assumed`, its criticality is high/medium, and it isn't model-inferable. On `ready`, `serialise_plan_for_system` injects a "GATEWAY PLAN … Resolved inputs: …" block into the compute system prompt (`backend/chat/system_blocks.py:82`–`83`).

**Why this collides with the scenario — and the fix.** The gateway sees only the latest user message (`backend/chat/orchestrator.py:133`). On "what if they were married?", income/region/year/children are not in that message, so they surface as `assumed` or absent. Two bad outcomes follow: the gateway either fires `needs_plan` and **re-asks the user for things they already gave**, or routes on a half-grounded plan. That is precisely the lossy re-derivation this issue exists to kill, now reproduced one layer earlier.

The fix makes the scenario the gateway's missing memory:

1. **Seed the gateway from the persisted scenario.** `run_gateway` gains a `prior_scenario` argument (passed from the loop at `backend/gateway/runtime.py:133`'s call site). Slots whose value is already pinned by the scenario are treated as **grounded** — either fed as context into the `emit_plan` call, or post-processed in `_verdict_from_plan` (`backend/gateway/runtime.py:113`) so a scenario-backed slot is promoted from `assumed` to a grounded source before `gate()` runs. Net effect: a follow-up that only changes `marital_status` no longer gates on income/region/year, so the gateway stops re-asking. This *strengthens* the gateway's anti-over-asking design (its `INFERABLE` set in `backend/gateway/policy.py`) instead of fighting it.

2. **Let the gateway plan auto-update the scenario.** The gateway already extracts grounded slot values each `ready` turn. Rather than depending entirely on the writer remembering to call `update_scenario`, the loop can fold the gateway's grounded `ready`-plan slots into the scenario at end of turn (a server-side derive), with `update_scenario` retained for mid-turn corrections and fields the gateway didn't capture (e.g. `comparison_baseline`, `notes`). This is a real simplification of §3.2: `get_scenario`/`update_scenario` stay for the writer, but the scenario is *seeded and refreshed* from the plan the gateway already computes — less reliance on agent discipline.

3. **Merge the two per-turn blocks.** The scenario snapshot (§3.3) and `serialise_plan_for_system`'s gateway-plan block target the same architectural slot — a per-turn, post-cache-breakpoint "here is the resolved state" block (`backend/chat/system_blocks.py:82`–`83`). They should be **composed into one block** ("ACTIVE SCENARIO + this turn's resolved plan"), not stacked as two overlapping ones, so the model gets a single coherent state view and we don't double-spend tokens describing the same household twice.

4. **Outcome interactions.** Scenario writes happen only on `ready`/compute turns (tools exist only there — see §3.2). On a `needs_plan` clarifying turn, or `out_of_scope`/`irrelevant`/`partial`, the lightweight no-tool writer runs, so the agent can *reference* the injected scenario when phrasing its clarifying question but applies changes on the next `ready` turn. Crucially, seeding (point 1) should *reduce* how often `needs_plan` fires at all, which is the user-visible win.

**Open design tension (Q5 below):** how much of `update_scenario` survives once the gateway auto-derives the scenario from its grounded plan. The recommendation is to keep the explicit tool (the writer often knows intent the single-message gateway can't infer — e.g. "use last year's figures", baseline choice), but treat the gateway plan as the *default* writer of household/reform slots.

---

## 4. Data flow — worked example

User turn 1: **"Single earner £45k Scotland — marginal rate at £60k?"**

1. Frontend POSTs `/chat/message` with `messages`, `active_scenario: null` (`frontend/src/app/ChatPage.tsx:648`).
2. The gateway grounds a plan from the message (income/region present → `source: prompt`), outcome `ready` (`backend/gateway/runtime.py:146`). Loop seeds `scenario = None`; `_build_system_blocks` injects the gateway-plan block but no scenario snapshot (`backend/chat/orchestrator.py:143`–`146`).
3. Agent calls `update_scenario({"patch": {"household": {"earners": [{"employment_income": 45000, "marital_status": "single"}], "children": 0, "country": "Scotland", "year": 2025}}})` — or the loop derives the same from the grounded plan (§3.6.2). The loop merges it (§3.2) and returns the new state.
4. Agent calls a compute tool (`run_python` / `calculate_household`, `backend/tools/dispatch.py:291` / `backend/tools/definitions.py:214`) for the marginal rate at £60k, answers in prose.
5. The `done` event (`backend/chat/orchestrator.py:264`) carries `active_scenario`. Frontend `setActiveScenario(...)`, renders the pill "Single earner, £45k, Scotland", and `saveConversation` persists `{messages, active_scenario}` to Postgres (`backend/conversations/store.py:20`).

User turn 2: **"What if they were married?"**

6. Frontend POSTs again, now with `active_scenario` = the stored object.
7. **The gateway is seeded from the scenario** (§3.6.1): income/region/year are grounded from prior state, so `married` is the only change and the gateway does **not** fire `needs_plan` — outcome `ready`. The loop injects the merged scenario + plan block (§3.3/§3.6.3), so the agent sees `marital_status: "single"`, £45k, Scotland *without* re-reading scrollback.
8. Agent calls `update_scenario({"patch": {"household": {"earners": [{"employment_income": 45000, "marital_status": "married"}]}}})` — only the changed field, everything else inherited.
9. Agent re-runs the compute tool and answers the married case, optionally contrasting with the single case.
10. `done` returns the updated scenario; pill updates to "Married, £45k, Scotland"; saved again.

The follow-up never restated income, region, year, or children — and the gateway never re-asked for them. That is the whole point of the feature.

---

## 5. Open questions (issue items + recommendations)

**Q1 — Granularity: per-conversation or per-message?**
The issue leans per-conversation with forking. **Recommendation: per-conversation.** One `active_scenario` column on `chat_conversations`, owned by the client and resent each turn (mirrors how `messages` already round-trip). Per-message snapshots would bloat the JSON and complicate the upsert; we can add an explicit "fork scenario" action later (clone the object into a new `session_id`). Sub-question: should we snapshot the scenario *into each saved message* for replay/debugging? Lightweight and useful for the report flow (`backend/conversations/reports.py`) — proposed as a **phase 5** nicety, not v1.

**Q2 — Display: how much in the pill vs the modal?**
**Recommendation: one-line summary in the pill, full JSON in a click-through modal.** Pill = `summariseScenario()` ("Married, £45k, Scotland"); modal = pretty-printed structure + "Reset". Matches the existing compact-pill aesthetic of the Charts button (`frontend/src/app/ChatPage.tsx:1821`–`1839`).

**Q3 — Interaction with gateway outcomes (was: conflict with Plan mode).**
Plan mode has been removed; its "ask before doing" role is now the gateway's `needs_plan` outcome. Because non-`ready` outcomes route to the no-tool lightweight writer (`backend/chat/system_blocks.py:87`), the agent **cannot** patch the scenario on a clarifying turn — it can only *read* the injected snapshot and ask its question, applying the change on the next `ready` turn. **Recommendation: this is the correct behaviour**, and the gateway seeding in §3.6.1 makes `needs_plan` fire *less* on follow-ups, which is the better fix than any prompt wording. No structural conflict; the only prompt work is a line in `GATEWAY_NEEDS_PLAN_DIRECTIVE` (`backend/prompts/gateway.py:121`) noting the agent may reference the active scenario when forming its question.

**Q4 — Relationship to the typed tools (now live).**
`calculate_household` and `run_economy_simulation` are registered (`backend/tools/definitions.py:214`, `:225`) and dispatched (`backend/tools/dispatch.py`), with reforms keyed by programme through `build_compiled_policy` (`:35`). **Recommendation: the scenario's `household`/`reform` shape should be exactly the args those tools accept**, so a persisted scenario feeds a typed call with no translation. The earlier "sequence this after typed tools land / don't assume they're registered" caveat is resolved — they are live, so we design the schema to their current signatures from day one.

**Q5 — How much of `update_scenario` survives gateway auto-derive? (new)**
Given §3.6.2, the gateway can populate household/reform slots from its grounded plan without the writer calling `update_scenario`. **Recommendation: keep the explicit tool but make the gateway plan the default writer.** The tool still earns its place for intent the single-message gateway can't infer (comparison baseline, "use last year's figures", clearing a reform), and as the Plan-less path's only way to mutate state. Revisit collapsing it entirely once we have eval data on how often the gateway plan alone is sufficient.

---

## 6. Phased implementation plan

Each phase is an independently shippable PR.

- **PR 1 — Backend state plumbing (no persistence, no UI).**
  Add `get_scenario`/`update_scenario` to `TOOL_DEFINITIONS` (`backend/tools/definitions.py`) and `_merge_scenario` to `backend/tools/`; handle them in the chat loop (Option A, §3.2); add the per-turn snapshot block to `_build_system_blocks` (`backend/chat/system_blocks.py`), composed with the gateway-plan block (§3.6.3); add `active_scenario` to `ChatRequest` and the `done` event. Add the static prompt rules to `backend/prompts/system.py`. Unit-test `_merge_scenario` (clear, deep-merge household, reform reset). Fully additive within a single streamed session. *Risk: low.*

- **PR 2 — Gateway seeding.**
  Thread `prior_scenario` into `run_gateway` (`backend/gateway/runtime.py`) and promote scenario-backed slots to grounded before `gate()` (§3.6.1); optionally auto-derive scenario updates from the grounded `ready` plan (§3.6.2). Add a gateway-policy unit test: a follow-up that changes one slot with the rest supplied by the scenario must **not** produce `needs_plan`. *Risk: medium — touches routing; gate logic is pure and unit-testable offline.*

- **PR 3 — Persistence round-trip.**
  Add the `active_scenario` column + migration in `ensure_table` (`backend/conversations/models.py`); extend `SaveConversationRequest` / `ConversationDetail` / save/get/shared handlers (`backend/conversations/`). Frontend: store the scenario from `done`, resend it each request, include it in `saveConversation`, hydrate in `loadConversation`, clear on new chat. *Risk: low; column is nullable, old rows unaffected.*

- **PR 4 — Pill + modal UI.**
  `summariseScenario` helper, the pill in the input chrome (`ChatPage.tsx` toggle row), the modal, clear/reset wiring. *Risk: low; UI-only.*

- **PR 5 — Polish & edges (optional).**
  `GATEWAY_NEEDS_PLAN_DIRECTIVE` wording (Q3); per-message scenario snapshot for the report/debug flow (Q1 sub-question); eval cases for the `single → married` flow and the no-re-ask gateway behaviour. *Risk: low.*

---

## 7. Risks & alternatives

**Risk: agent forgets to call `update_scenario`.** Memory is only as good as the agent's discipline. Mitigations: inject the live snapshot every turn (§3.3) so even without writing back, the agent always *sees* prior state; **auto-derive scenario slots from the gateway's grounded plan** (§3.6.2) so the common household/reform fields don't depend on a writer call at all; reinforce with prompt rules and an eval case (the `single → married` flow). The snapshot-on-read plus gateway-derive together make the feature robust to a missed write.

**Risk: stale or wrong scenario silently corrupts answers.** If the scenario is patched incorrectly, later turns inherit the error — and now the *gateway* inherits it too (seeding, §3.6.1), so a wrong region could suppress a clarifying question that should have fired. Mitigations: every number still comes from a fresh compute call (prompt rule); the user-visible pill makes drift *observable* (they can see "Scotland" went missing and reset); the gateway treats scenario-backed slots as grounded but the writer still verifies the plan against the message (`serialise_plan_for_system` already instructs "verify against the user's message"). Observability + verify-against-message is the antidote.

**Risk: unvalidated JSON blob grows / gets malformed.** The scenario is advisory and free-ish. Mitigation: a hard size cap (e.g. 8 KB) and a JSON-shape check in `_merge_scenario`; reject patches that aren't objects. We deliberately *don't* Pydantic-validate in v1 (alternative below).

**Risk: client-owned state can be tampered with.** Since the client resends `active_scenario`, a malicious client could inject arbitrary JSON. But it only flows back into the prompt as advisory text (and into the gateway as slot hints) and is never `exec`'d — the sandboxed `run_python` (`backend/tools/dispatch.py:291`) is the only execution path and is unaffected. Acceptable for v1; same trust model as the client already resending `messages`.

**Risk: two writers (client `saveConversation` vs a server-side scenario write).** Avoided by choosing the client-owned path (§3.4, Q1) — single writer, consistent with `messages`.

**Alternative A — server-authoritative scenario, validated by a Pydantic model.**
Tighter and self-documenting, but: (1) it diverges from the current client-owns-transcript architecture and adds a racing DB writer; (2) a rigid schema fights the agent when the user says something the model can't cleanly express. Rejected for v1; revisit once the schema stabilizes against the live typed-tool signatures (Q4), at which point a validated model becomes attractive — and would compose well with the gateway plan, which is already structured.

**Alternative B — no tools; derive the scenario from message history with a cheap model each turn.**
We already run fast-model passes for titles and follow-up suggestions (`backend/chat/titles.py`, `backend/chat/suggestions.py`), and the gateway is itself a cheap per-turn pass. But a *separate* summarisation re-introduces the lossy re-derivation this issue kills and costs an extra call. Note this is partly subsumed by §3.6.2: the gateway plan *is* a cheap structured per-turn extraction we already pay for, so we harvest it rather than adding a third model call. Rejected as a standalone mechanism.

**Alternative C — store the scenario only in browser localStorage.**
Zero backend change, but breaks the issue's explicit requirement that the scenario survive sharing and cross-device reloads (the conversation already persists server-side; the scenario should travel with it). Rejected.
