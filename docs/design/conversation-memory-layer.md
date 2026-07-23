# Design doc: conversation memory layer — persist households/scenarios across turns

Status: **Draft for discussion** (issue [#86](https://github.com/PolicyEngine/policyengine-uk-chat/issues/86), tagged `needs-design`)
Author: design discussion — no implementation yet
Scope: backend (`backend/chat/`, `backend/tools/`, `backend/conversations/`, `backend/gateway/`, `backend/prompts/`) + frontend (`frontend/src/app/ChatPage.tsx`)

> This is a design doc to agree the shape before any code. All `file:line` references are against `main` (post-#94 typed tools, post-#112 gateway, post-#116 backend repackaging).

> **Refresh note (re-grounded after #94, #112 and #116; incorporates @anth-volk's review).** The original draft was written against the pre-`#116` layout (`backend/routes/chatbot.py`, `backend/agent_tools.py`, `backend/routes/conversations.py`) and before the gateway. Four things changed the design, not just the file paths:
> 1. **The backend was repackaged (#116).** The monolithic `routes/chatbot.py` is now the `chat/` package (`orchestrator.py`, `system_blocks.py`, `schemas.py`); tools are the `tools/` package (`definitions.py`, `dispatch.py`); conversations are the `conversations/` package; system prompts are the `prompts/` package. All refs below point at the new tree (this is one step past the refs in @anth-volk's review, which predate #116).
> 2. **Typed tools landed and were later split into a policyengine.py lifecycle.** `TOOL_DEFINITIONS` now registers discovery, validation, simulation, derivative, and artifact tools (`backend/tools/definitions.py`) dispatched through the registry-backed `execute_tool` path (`backend/tools/dispatch.py`). The scenario's `reform`/`household` shape should align to `run_household_simulation` and `run_society_simulation`.
> 3. **The gateway landed (#112).** A cheap pre-pass (`backend/gateway/`) grounds a per-turn *plan* (tool + slot values) before the heavy model runs — overlapping directly with the scenario this doc proposes. See the new [§3.6](#36-reconciliation-with-the-gateway-112). **Plan mode was removed**; its "ask before doing" role is now the gateway's `needs_plan` outcome, so the old "conflict with Plan mode" question is recast in [Q3](#5-open-questions-issue-items--recommendations).
> 4. **Tool surface tightened to one write tool (per review §3).** Dropped `get_scenario`; the always-injected snapshot already gives the agent the state for free, so a separate read tool is dead weight. See [§3.2](#32-the-update_scenario-tool-one-write-tool--passive-snapshot).

---

## 1. Problem & motivation

Today every chat turn is effectively one-shot. The agent has no structured memory of the household or reform it analysed on the previous turn — the only "memory" is the raw message transcript, which the model re-reads each turn.

Concretely, the streaming loop `stream_chat` (`backend/chat/orchestrator.py`) rebuilds the request from `chat_request.messages` (`ChatRequest`, `backend/chat/schemas.py`) every turn, prepends the cached system prompt via `_build_system_blocks` (`backend/chat/system_blocks.py`), and the agent answers quantitative questions through registered lifecycle tools (`backend/tools/definitions.py`) — typically `run_household_simulation`, `run_society_simulation`, derivative tools, and `generate_chart` (`backend/tools/dispatch.py`). When a user says:

> "Single earner, £45,000, in Scotland — what's the marginal rate at £60k?"

…then follows up with:

> "What if they were married?"

…the agent must **re-derive the entire household** from scrollback to mutate one field. This is lossy: details silently drift (the country gets dropped, the income changes, the child count resets), the model re-pays tokens to reconstruct context, and comparative conversations — arguably the killer feature of a tax/benefit chatbot — feel brittle.

There is now a second consumer of that lost context: the **gateway** (`run_gateway`, `backend/gateway/runtime.py`) only ever sees the *last user message* (`backend/chat/orchestrator.py`). On the follow-up turn it sees just "what if they were married?" — so income, region, year and child count come back as `assumed`/missing slots, and the gateway either re-asks for them (`needs_plan`) or routes on a half-grounded plan. The same missing memory that hurts the writer now also degrades routing. See [§3.6](#36-reconciliation-with-the-gateway-112).

The proposal: persist a structured **active scenario** (household + reform + comparison baseline) per conversation. The agent reads it (from an always-injected snapshot) at the top of each turn and writes patches to it as the user introduces or modifies parameters, instead of re-extracting everything from text — and the gateway is seeded from it so it stops re-asking for inherited values.

---

## 2. Goals / non-goals

### Goals
- Persist a single structured `active_scenario` per conversation, surviving across turns, reloads, and shared links.
- Inject the live scenario as a per-turn system block so the agent always *sees* prior state (the safety net), and give the agent **one** tool, `update_scenario(patch)` (shallow-merge mutate), to change it — no separate `get_scenario` (see [§3.2](#32-the-update_scenario-tool-one-write-tool--passive-snapshot)).
- Instruct the agent (via the system prompt) to patch the scenario whenever the user introduces/modifies household or reform parameters.
- Round-trip the scenario through the existing conversation save/load path (`backend/conversations/`) so it is durable and shareable.
- Show a subtle "active scenario" pill in the input chrome of `ChatPage.tsx`, with clear/reset.
- **Seed the gateway from the scenario** so inherited slots count as grounded and the gateway stops re-asking on follow-ups ([§3.6](#36-reconciliation-with-the-gateway-112)).
- Align the scenario's `household`/`reform` shape to the now-live simulation tools (`run_household_simulation`, `run_society_simulation`) so a persisted scenario feeds a typed call with no translation.
- Land the canonical `single → married` regression as an **eval case in PR 1**, so the memory behaviour is enforced deterministically, not just prompted.

### Non-goals (for v1)
- **Not** building the typed `household`/`reform` execution tools — those have already landed. This layer *persists* structured args; it does not change the tool lifecycle.
- **Not** multi-scenario / named-scenario management (A vs B vs C side by side). One active scenario per conversation; forking is a future extension.
- **Not** server-authoritative scenario derivation from microsim *outputs*. The agent (and the gateway plan) own the scenario contents; the server stores and relays them in v1. We do not parse the household back out of computed results. (Server-*authoritative storage* keyed by `session_id` is a named v2 target — see [§3.4](#34-server-side-persistence).)
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
    "country": "SCOTLAND",   // ENGLAND | NORTHERN_IRELAND | SCOTLAND | WALES
    "year": 2025
  },
  "reform": null,            // null = current law, else a reform dict (see below)
  "comparison_baseline": "current_law",
  "notes": null              // free-text the agent may use for nuance the schema can't hold
}
```

The `reform` field intentionally mirrors the flat policyengine.py parameter-path reform structure the compute path accepts. That means a persisted `reform` can be handed straight to `validate_reform`, `run_household_simulation`, or `run_society_simulation` (`backend/tools/definitions.py`) with no translation. Typed args make the scenario *expressible*; this issue makes them *durable*.

**Schema home (per review — "schema home" structural ask).** The canonical shape above and the `_merge_scenario` helper land in the `tools/` package alongside the tool schemas they mirror (`backend/tools/`), **not** inline in the orchestrator loop; the static prompt rules land in `backend/prompts/system.py`; the per-turn injection lives in `backend/chat/system_blocks.py`. This keeps the #94/#116 split intact (schemas/helpers in `tools/`, prompt text in `prompts/`, assembly in `chat/`).

Design choice: the schema is **advisory, not validated** server-side in v1. The server stores whatever the agent writes (after a size cap + JSON-shape check). Reasoning in §7.

### 3.2 The `update_scenario` tool (one write tool + passive snapshot)

The original draft proposed *two* tools — `get_scenario` (read) and `update_scenario` (write) — plus an always-injected snapshot. Per @anth-volk's review §3, the read tool and the snapshot overlap: if the canonical state is already in the agent's context every turn (§3.3), `get_scenario` has almost nothing to do, while costing an extra entry in the cached `TOOL_DEFINITIONS` array, an extra branch in the loop, and one more thing the model can call needlessly. **We collapse to one write tool + the passive snapshot.** The only thing that would re-justify a read tool is if we ever *truncate* the injected snapshot for size (§7); that decision and the read-tool decision are deliberately coupled — drop `get_scenario` now, reintroduce it only if and when we truncate.

Tools are declared in `TOOL_DEFINITIONS` (`backend/tools/definitions.py`) and dispatched via `TOOL_HANDLERS[name](**input)` inside `execute_tool` (`backend/tools/dispatch.py`). Adding a tool normally means: write the function, add it to `TOOL_HANDLERS`, add a schema entry to `TOOL_DEFINITIONS`.

**The wrinkle — and why the loop, not `execute_tool`, must own it.** Every existing handler is *stateless* — it takes only its declared inputs and touches no conversation state. `update_scenario` reads/writes **per-conversation state**, which `execute_tool` has no handle on. More importantly, tool dispatch is now **concurrent**: the loop fans tool calls out as `execute_tool_async` tasks via `asyncio.ensure_future` and gathers them with `asyncio.as_completed` (`backend/chat/orchestrator.py`). If `update_scenario` were dispatched into that gather, a turn that emits it alongside other tool calls could patch a shared mutable `scenario` dict from racing tasks. So handling the scenario tool **synchronously in the loop, outside the gather** isn't just cleaner — it's necessary for correctness. Two viable approaches:

**Option A — handle `update_scenario` in the chat loop, keep `execute_tool` stateless (recommended).**
Keep `update_scenario` in `TOOL_DEFINITIONS` (so the model sees it and the cache-stamping helper `_tool_defs_for_anthropic` at `backend/chat/system_blocks.py` forwards it), but intercept it in the loop *before* building the `execute_tool_async` tasks. The loop already gathers `tool_uses` (`backend/chat/orchestrator.py`); split `update_scenario` out, apply it synchronously to the in-scope `scenario` dict, and only fan the remaining (stateless) tools into the concurrent gather:

  ```python
  # before the gather (around the tool fan-out in backend/chat/orchestrator.py):
  compute_uses = []
  for tu in tool_uses:
      if tu["name"] == "update_scenario":
          scenario = _merge_scenario(scenario, tu["input"].get("patch", {}))   # serial, no race
          completed_tools[tu["id"]] = {"status": "ok", "active_scenario": scenario}
      else:
          compute_uses.append(tu)
  tasks = [asyncio.ensure_future(execute_tool_async(tu)) for tu in compute_uses]
  ```
  `_merge_scenario` lives in `backend/tools/` (pure, unit-testable): shallow-merge top-level keys, with `household` merged one level deeper so `{"household": {"earners": [...]}}` doesn't clobber `country`/`children`. `patch == {"reform": null}` explicitly clears the reform. Because it's a pure dict op with no I/O, doing it synchronously costs nothing.

**Option B — pass a mutable context into `execute_tool`.**
Change `execute_tool(name, input, context=None)` and register a stateful handler that reads/writes `context["scenario"]`. More uniform, but it touches the signature every handler and every test in `backend/tests/` depends on, the executor call site, *and* still has to solve the concurrency race. Heavier; rejected.

> **Tools exist only on the `ready` route.** The gateway routes non-`ready` outcomes (`irrelevant` / `out_of_scope` / `partial` / `needs_plan`) to a lightweight, **no-tool** reply turn (`_build_lightweight_system_blocks`, `backend/chat/system_blocks.py`; `route="lightweight"`), so `update_scenario` can only be called on `ready`/compute turns. This is the structural successor to the old Plan-mode tool omission — see [Q3](#5-open-questions-issue-items--recommendations).

> Note on prompt caching: `_tool_defs_for_anthropic` (`backend/chat/system_blocks.py`) stamps `cache_control` on the **last** tool only, so the whole tool array caches as one block. Appending one tool to `TOOL_DEFINITIONS` is fine — the cache breakpoint just moves to the new last tool; keep the ordering stable. (The gateway's `emit_plan` tool is deliberately *not* in `TOOL_DEFINITIONS` — `backend/gateway/runtime.py` — so it never enters this array.)

**`TOOL_DEFINITIONS` entry** (appended to the list at `backend/tools/definitions.py`):

```python
{
  "name": "update_scenario",
  "description": "Shallow-merge a patch into the conversation's active scenario "
                 "(household, reform, comparison_baseline, notes). The current "
                 "scenario is already shown to you each turn, so you never need "
                 "to read it back — only call this to CHANGE it. Call it whenever "
                 "the user introduces or modifies household or reform parameters "
                 "(income, region, marital status, children, year, reform, "
                 "comparison baseline). Pass only the changed fields. Set a field "
                 "to null to clear it (e.g. {\"reform\": null}).",
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

The system prompt is assembled by `_build_system_blocks` (`backend/chat/system_blocks.py`): a cached `SYSTEM_PROMPT` block (`backend/prompts/system.py`, assembled from `SYSTEM_PROMPT_SECTIONS`), then optional **per-turn** directives appended after the cache breakpoint — Charts mode (`backend/chat/system_blocks.py`) and the **gateway plan** (fed by `serialise_plan_for_system`) — so toggling them never invalidates the cache.

We add scenario guidance in two parts:

1. **Static behavioural rules** → appended to `SYSTEM_PROMPT_SECTIONS` (`backend/prompts/system.py`), inside the cached block. Roughly:
   > CONVERSATION SCENARIO MEMORY:
   > - A structured `active_scenario` persists across turns and is shown to you each turn under "ACTIVE SCENARIO". Treat it as the current household/reform unless the user changes it; do not re-derive it from the transcript.
   > - Whenever the user introduces or changes any household/reform parameter, call `update_scenario(patch)` with only the changed fields, then compute.
   > - If a change is ambiguous (e.g. "make them richer"), ask a brief clarifying question before patching — do not invent values.
   > - The scenario is advisory context for *you*; every number in your answer must still come from a compute tool, not from the scenario.

2. **Live scenario snapshot** → injected as a **per-turn** block in `_build_system_blocks`, after the cache breakpoints, **composed with the existing gateway-plan block** rather than added as a third parallel block (see [§3.6](#36-reconciliation-with-the-gateway-112)). `_build_system_blocks` gains a `scenario: dict | None = None` parameter, and the orchestrator passes the loaded scenario alongside `gateway_plan` (around `backend/chat/orchestrator.py`):
   ```python
   if scenario:
       blocks.append({"type": "text",
                      "text": "ACTIVE SCENARIO (current persisted state):\n"
                              + json.dumps(scenario, indent=2)})
   ```
   This passive snapshot is the safety net: the agent sees the state for free every turn, so the feature is robust even if the model forgets to write back. It is also why there is no `get_scenario` (§3.2). Because it's after the cache breakpoint, a changing scenario never busts the cached prompt — it mirrors the existing per-turn-directive pattern precisely.

### 3.4 Server-side persistence

Conversations persist through `backend/conversations/`: a SQLModel `ChatConversation` table (`backend/conversations/models.py`) backed by `DATABASE_URL` (Postgres — in production the Supabase Postgres instance; distinct from the `supabase` client used only for billing under `backend/billing/`). Messages are stored as a JSON string in the `messages` column (`backend/conversations/models.py`); save is upsert-by-`session_id` in `save_conversation` (`backend/conversations/store.py`); load is by id in `get_conversation` (`backend/conversations/store.py`); shared load is `get_shared_conversation` (`backend/conversations/sharing.py`).

We add one nullable column, `active_scenario` (TEXT, JSON-encoded), to `ChatConversation`. The migration pattern already exists: `ensure_table` (`backend/conversations/models.py`) idempotently `ALTER TABLE ... ADD COLUMN` for new columns. We extend that loop with `("active_scenario", "TEXT")`. Then `SaveConversationRequest` (`backend/conversations/schemas.py`) and `ConversationDetail` gain `active_scenario`, and the save/get/shared handlers (de)serialize it.

**Who owns the canonical scenario at rest? (review §4 — make this choice explicit.)**

- **v1 — client-owned, resent each turn (recommended to ship).** The chat loop holds the live scenario during a streamed turn; the `done` event (`backend/chat/orchestrator.py`) returns it; the frontend stores it and includes it in the next request and in `saveConversation`. This keeps the server stateless between requests and mirrors exactly how `messages` already flow — the client owns the transcript and resends it each turn (`ChatRequest.messages`, `backend/chat/schemas.py`; `frontend/src/app/ChatPage.tsx`). Single writer (the client's `saveConversation` upsert), so no write race. The tamper surface is genuinely small: the scenario re-enters only as advisory prompt text and gateway slot hints, then must still pass normal validation before calculation.
- **v2 — server-authoritative, keyed by `session_id` (named target, not dismissed).** The more robust pattern, and where agent frameworks generally point, is server-side state: `chat_conversations` is *already* keyed by `session_id` with an idempotent migration, so a server-authoritative scenario (single writer on one nullable column, last-write-wins, not touching `messages`) is natural here, not exotic. The "two racing writers" objection is solvable by making the server the sole writer of that column. We ship client-owned for v1 for consistency and minimal surface, but **call server-authoritative the explicit v2 target** so we don't bake the client-owned assumption in permanently. This requires adding `active_scenario` to `ChatRequest` (`backend/chat/schemas.py`) for v1; v2 would drop it from the request and read from the row.

Round-trip (v1): client sends `active_scenario` → loop seeds the live scenario → agent (and/or gateway, §3.6) patches it → `done` returns it → client stores + POSTs it alongside `messages`. On reload, `get_conversation` returns it; the client re-seeds it next turn.

### 3.5 Frontend pill UI

`ChatPage.tsx` already has the right scaffolding:
- Per-conversation client state via `useState`, e.g. `messages`, `chartsMode` (`frontend/src/app/ChatPage.tsx`). (Note: `planMode` was removed alongside backend Plan mode, so the pill row now holds Charts only — the natural neighbour for an "Active scenario" pill.)
- The toggle/button row under the textarea (the Charts pill lives around `frontend/src/app/ChatPage.tsx`).
- Save/load already structured: `saveConversation`, `loadConversation` hydrating from `ConversationDetail`, and the chat request body.

Changes (line numbers approximate — `ChatPage.tsx` drifts):
- New state: `const [activeScenario, setActiveScenario] = useState<ActiveScenario | null>(null);` next to `chartsMode`.
- Read the scenario off the stream `done` event in the SSE handler → `setActiveScenario(data.active_scenario)`.
- Send it on the next request: add `active_scenario: activeScenario` to the request body.
- Persist it: add `active_scenario: activeScenario` to the `saveConversation` body; hydrate it in `loadConversation`; clear it in the "new chat" handler (`setMessages([])`).
- Render a pill in the toggle row, styled like the Charts pill, showing a **one-line summary** (e.g. "Single earner, £45k, Scotland") with an `×` to clear → `setActiveScenario(null)`. Clicking the pill body opens a small modal showing the full JSON structure with a "Reset scenario" button.
- A `summariseScenario(scenario)` helper produces the pill's one-liner from the canonical shape in §3.1.

### 3.6 Reconciliation with the gateway (#112)

This is the section the refresh adds, because the gateway changes the design rather than just sitting beside it.

**What the gateway already does.** Before the heavy model runs, `run_gateway(last_user_message)` (`backend/gateway/runtime.py`) makes one cheap forced-tool call (`emit_plan`) that fills a structured plan: `in_domain`, a best-fit `tool`, and a list of **slots** — `SlotFact(name, kind, value, source)` where `source ∈ {prompt, default, assumed}` (`backend/gateway/policy.py`). The pure `gate()` then maps that plan to one of five outcomes. A slot *gates* (forces a clarifying question, `needs_plan`) iff its `source` is `assumed`, its criticality is high/medium, and it isn't model-inferable. On `ready`, `serialise_plan_for_system` injects a "GATEWAY PLAN … Resolved inputs: …" block into the compute system prompt (`backend/chat/system_blocks.py`).

**Why this collides with the scenario — and the fix.** The gateway sees only the latest user message (`backend/chat/orchestrator.py`). On "what if they were married?", income/region/year/children are not in that message, so they surface as `assumed` or absent. Two bad outcomes follow: the gateway either fires `needs_plan` and **re-asks the user for things they already gave**, or routes on a half-grounded plan. That is precisely the lossy re-derivation this issue exists to kill, now reproduced one layer earlier.

The fix makes the scenario the gateway's missing memory:

1. **Seed the gateway from the persisted scenario.** `run_gateway` gains a `prior_scenario` argument (passed from the loop at `backend/gateway/runtime.py`'s call site). Slots whose value is already pinned by the scenario are treated as **grounded** — either fed as context into the `emit_plan` call, or post-processed in `_verdict_from_plan` (`backend/gateway/runtime.py`) so a scenario-backed slot is promoted from `assumed` to a grounded source before `gate()` runs. Net effect: a follow-up that only changes `marital_status` no longer gates on income/region/year, so the gateway stops re-asking. This *strengthens* the gateway's anti-over-asking design (its `INFERABLE` set in `backend/gateway/policy.py`) instead of fighting it.

2. **Let the gateway plan auto-update the scenario.** The gateway already extracts grounded slot values each `ready` turn. Rather than depending entirely on the writer remembering to call `update_scenario`, the loop can fold the gateway's grounded `ready`-plan slots into the scenario at end of turn (a server-side derive), with `update_scenario` retained for mid-turn corrections and fields the gateway didn't capture (e.g. `comparison_baseline`, `notes`). This further reduces reliance on agent discipline — and it is the strongest argument for keeping the one write tool small (§3.2): the gateway plan is the *default* writer of household/reform slots; `update_scenario` is the override.

3. **Merge the two per-turn blocks.** The scenario snapshot (§3.3) and `serialise_plan_for_system`'s gateway-plan block target the same architectural slot — a per-turn, post-cache-breakpoint "here is the resolved state" block (`backend/chat/system_blocks.py`). They should be **composed into one block** ("ACTIVE SCENARIO + this turn's resolved plan"), not stacked as two overlapping ones, so the model gets a single coherent state view and we don't double-spend tokens describing the same household twice.

4. **Outcome interactions.** Scenario writes happen only on `ready`/compute turns (tools exist only there — §3.2). On a `needs_plan` clarifying turn, or `out_of_scope`/`irrelevant`/`partial`, the lightweight no-tool writer runs, so the agent can *reference* the injected scenario when phrasing its clarifying question but applies changes on the next `ready` turn. Crucially, seeding (point 1) should *reduce* how often `needs_plan` fires at all, which is the user-visible win.

---

## 4. Data flow — worked example

User turn 1: **"Single earner £45k Scotland — marginal rate at £60k?"**

1. Frontend POSTs `/chat/message` with `messages`, `active_scenario: null` (`frontend/src/app/ChatPage.tsx`).
2. The gateway grounds a plan from the message (income/region present → `source: prompt`), outcome `ready` (`backend/gateway/runtime.py`). Loop seeds `scenario = None`; `_build_system_blocks` injects the gateway-plan block but no scenario snapshot (`backend/chat/orchestrator.py`).
3. Agent calls `update_scenario({"patch": {"household": {"earners": [{"employment_income": 45000, "marital_status": "single"}], "children": 0, "country": "SCOTLAND", "year": 2025}}})` — or the loop derives the same from the grounded plan (§3.6.2). The loop merges it synchronously (§3.2) and returns the new state.
4. Agent calls lifecycle tools (`run_household_simulation` and, if needed, derivative/chart tools) for the marginal rate at £60k, answers in prose.
5. The `done` event (`backend/chat/orchestrator.py`) carries `active_scenario`. Frontend `setActiveScenario(...)`, renders the pill "Single earner, £45k, Scotland", and `saveConversation` persists `{messages, active_scenario}` to Postgres (`backend/conversations/store.py`).

User turn 2: **"What if they were married?"**

6. Frontend POSTs again, now with `active_scenario` = the stored object.
7. **The gateway is seeded from the scenario** (§3.6.1): income/region/year are grounded from prior state, so `married` is the only change and the gateway does **not** fire `needs_plan` — outcome `ready`. The loop injects the merged scenario + plan block (§3.3/§3.6.3), so the agent sees `marital_status: "single"`, £45k, Scotland *without* re-reading scrollback.
8. Agent calls `update_scenario({"patch": {"household": {"earners": [{"employment_income": 45000, "marital_status": "married"}]}}})` — only the changed field, everything else inherited.
9. Agent re-runs the compute tool and answers the married case, optionally contrasting with the single case.
10. `done` returns the updated scenario; pill updates to "Married, £45k, Scotland"; saved again.

The follow-up never restated income, region, year, or children — and the gateway never re-asked for them. That is the whole point of the feature, and it is the exact `single → married` flow committed as an eval case in PR 1 (§6).

---

## 5. Open questions (issue items + recommendations)

**Q1 — Granularity: per-conversation or per-message?**
The issue leans per-conversation with forking. **Recommendation: per-conversation.** One `active_scenario` column on `chat_conversations`, owned by the client and resent each turn (mirrors how `messages` already round-trip). Per-message snapshots would bloat the JSON and complicate the upsert; we can add an explicit "fork scenario" action later (clone the object into a new `session_id`). Sub-question: snapshot the scenario *into each saved message* for replay/debugging? Lightweight and useful for the report flow (`backend/conversations/reports.py`) — proposed as a **phase 5** nicety, not v1.

**Q2 — Display: how much in the pill vs the modal?**
**Recommendation: one-line summary in the pill, full JSON in a click-through modal.** Pill = `summariseScenario()` ("Married, £45k, Scotland"); modal = pretty-printed structure + "Reset". Matches the existing compact-pill aesthetic of the Charts button (`frontend/src/app/ChatPage.tsx`).

**Q3 — Interaction with gateway outcomes (was: conflict with Plan mode).**
Plan mode has been removed; its "ask before doing" role is now the gateway's `needs_plan` outcome. Because non-`ready` outcomes route to the no-tool lightweight writer (`backend/chat/system_blocks.py`), the agent **cannot** patch the scenario on a clarifying turn — it can only *read* the injected snapshot and ask its question, applying the change on the next `ready` turn. **Recommendation: this is the correct behaviour**, and the gateway seeding in §3.6.1 makes `needs_plan` fire *less* on follow-ups, which is the better fix than any prompt wording. No structural conflict; the only prompt work is a line in `GATEWAY_NEEDS_PLAN_DIRECTIVE` (`backend/prompts/gateway.py`) noting the agent may reference the active scenario when forming its question.

**Q4 — Relationship to the typed tools (now live, #94).**
`TOOL_DEFINITIONS` registers lifecycle tools including `run_household_simulation` and `run_society_simulation` (`backend/tools/definitions.py`), dispatched through `execute_tool` (`backend/tools/dispatch.py`). Reforms are flat policyengine.py parameter-path dictionaries. **Recommendation: the scenario's `household`/`reform` shape is exactly the args those tools accept**, designed to their live signatures from day one.

**Q5 — Is even one tool too many? (was: how much of `update_scenario` survives)**
Given §3.6.2, the gateway can populate household/reform slots from its grounded plan without any writer tool call. **Recommendation: keep the single `update_scenario`, make the gateway plan the default writer.** The tool still earns its place for intent the single-message gateway can't infer (comparison baseline, "use last year's figures", clearing a reform) and is the only mutation path on a tools-present turn. The read side is already covered by the passive snapshot (§3.2/§3.3), so we are at the minimal surface: one write tool + one injected block. Revisit collapsing the write tool entirely only if eval data shows the gateway plan alone suffices.

---

## 6. Phased implementation plan

Each phase is an independently shippable PR.

- **PR 1 — Backend state plumbing + eval (no persistence, no UI).**
  Add `update_scenario` to `TOOL_DEFINITIONS` (`backend/tools/definitions.py`) and `_merge_scenario` to `backend/tools/`; handle it synchronously in the chat loop, outside the concurrent gather (Option A, §3.2); add the per-turn snapshot block to `_build_system_blocks` (`backend/chat/system_blocks.py`), composed with the gateway-plan block (§3.6.3); add `active_scenario` to `ChatRequest` and the `done` event; add the static prompt rules to `backend/prompts/system.py`. Unit-test `_merge_scenario` (clear, deep-merge household, reform reset). **Commit the `single → married` eval case** to the harness so the memory behaviour is enforced, not just prompted. Fully additive within a single streamed session. *Risk: low.*

- **PR 2 — Gateway seeding.**
  Thread `prior_scenario` into `run_gateway` (`backend/gateway/runtime.py`) and promote scenario-backed slots to grounded before `gate()` (§3.6.1); optionally auto-derive scenario updates from the grounded `ready` plan (§3.6.2). Add a gateway-policy unit test: a follow-up that changes one slot with the rest supplied by the scenario must **not** produce `needs_plan`. *Risk: medium — touches routing; gate logic is pure and unit-testable offline.*

- **PR 3 — Persistence round-trip.**
  Add the `active_scenario` column + migration in `ensure_table` (`backend/conversations/models.py`); extend `SaveConversationRequest` / `ConversationDetail` / save/get/shared handlers (`backend/conversations/`). Frontend: store the scenario from `done`, resend it each request, include it in `saveConversation`, hydrate in `loadConversation`, clear on new chat. *Risk: low; column is nullable, old rows unaffected.*

- **PR 4 — Pill + modal UI.**
  `summariseScenario` helper, the pill in the input chrome (`ChatPage.tsx` toggle row), the modal, clear/reset wiring. *Risk: low; UI-only.*

- **PR 5 — Polish & edges (optional).**
  `GATEWAY_NEEDS_PLAN_DIRECTIVE` wording (Q3); per-message scenario snapshot for the report/debug flow (Q1 sub-question); server-authoritative storage as the v2 migration (§3.4). *Risk: low.*

---

## 7. Risks & alternatives

**Risk: agent forgets to call `update_scenario`.** Memory is only as good as the agent's discipline. Mitigations: the passive snapshot injected every turn (§3.3) means even without writing back, the agent always *sees* prior state; **auto-derive scenario slots from the gateway's grounded plan** (§3.6.2) so the common household/reform fields don't depend on a writer call at all; reinforce with prompt rules and the `single → married` eval case (PR 1). Snapshot-on-read plus gateway-derive together make the feature robust to a missed write.

**Risk: stale or wrong scenario silently corrupts answers.** If the scenario is patched incorrectly, later turns inherit the error — and now the *gateway* inherits it too (seeding, §3.6.1), so a wrong region could suppress a clarifying question that should have fired. Mitigations: every number still comes from a fresh compute call (prompt rule); the user-visible pill makes drift *observable* (they can see "Scotland" went missing and reset); the gateway treats scenario-backed slots as grounded but the writer still verifies the plan against the message (`serialise_plan_for_system` already instructs "verify against the user's message"). Observability + verify-against-message is the antidote.

**Risk: unvalidated JSON blob grows / gets malformed.** The scenario is advisory and free-ish. Mitigation: a hard size cap (e.g. 8 KB) and a JSON-shape check in `_merge_scenario`; reject patches that aren't objects. We deliberately *don't* Pydantic-validate in v1 (alternative below). **Note the coupling to §3.2:** if we ever hit the cap and start *truncating* the injected snapshot, that is the one scenario where a `get_scenario` read tool earns its place (the agent needs the full object the snapshot omits) — so the cap decision and the read-tool decision are made together, not separately.

**Risk: client-owned state can be tampered with.** Since the client resends `active_scenario`, a malicious client could inject arbitrary JSON. But it only flows back into the prompt as advisory text and gateway slot hints; any calculation still goes through validation and typed tool schemas. Acceptable for v1; same trust model as the client already resending `messages`. Server-authoritative storage (§3.4 v2) removes this surface entirely.

**Risk: concurrent dispatch races the scenario write.** Tool dispatch is concurrent (`asyncio.as_completed` over `execute_tool_async`, `backend/chat/orchestrator.py`). Mitigation: handle `update_scenario` synchronously in the loop *outside* the gather (§3.2) so the shared `scenario` dict is never patched from racing tasks. This is why the tool is loop-handled rather than a `TOOL_HANDLERS` entry.

**Risk: two writers (client `saveConversation` vs a server-side scenario write).** Avoided in v1 by the client-owned path (§3.4) — single writer, consistent with `messages`. v2 resolves it the other way: the server becomes the sole writer of the `active_scenario` column.

**Alternative A — server-authoritative scenario, validated by a Pydantic model.**
Tighter and self-documenting. It is the §3.4 v2 direction; the only reason it's not v1 is to keep the surface minimal and consistent with the client-owned transcript. Revisit once the schema stabilizes against the live typed-tool signatures (Q4), at which point a validated model becomes attractive — and composes well with the gateway plan, which is already structured.

**Alternative B — no tools; derive the scenario from message history with a cheap model each turn.**
We already run fast-model passes for titles and follow-up suggestions (`backend/chat/titles.py`, `backend/chat/suggestions.py`), and the gateway is itself a cheap per-turn pass. A *separate* summarisation re-introduces the lossy re-derivation this issue kills and costs an extra call. Partly subsumed by §3.6.2: the gateway plan *is* a cheap structured per-turn extraction we already pay for, so we harvest it rather than adding a third model call. Rejected as a standalone mechanism.

**Alternative C — store the scenario only in browser localStorage.**
Zero backend change, but breaks the issue's explicit requirement that the scenario survive sharing and cross-device reloads (the conversation already persists server-side; the scenario should travel with it). Rejected.
