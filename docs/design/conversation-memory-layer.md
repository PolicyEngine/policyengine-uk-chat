# Design doc: conversation memory layer — persist households/scenarios across turns

Status: **Draft for discussion** (issue [#86](https://github.com/PolicyEngine/policyengine-uk-chat/issues/86), tagged `needs-design`)
Author: design discussion — no implementation yet
Scope: backend (`backend/routes/chatbot.py`, `backend/agent_tools.py`, `backend/routes/conversations.py`) + frontend (`frontend/src/app/ChatPage.tsx`)

> This is a design doc to agree the shape before any code. All `file:line` references are against the state of `main` at the time of writing.

---

## 1. Problem & motivation

Today every chat turn is effectively one-shot. The agent has no structured memory of the household or reform it analysed on the previous turn — the only "memory" is the raw message transcript, which the model re-reads each turn.

Concretely, the streaming loop in `chat_message` (`backend/routes/chatbot.py:361`) rebuilds the request from `chat_request.messages` every turn (`backend/routes/chatbot.py:378`), prepends the static system prompt via `_build_system_blocks` (`backend/routes/chatbot.py:173`), and the agent answers quantitative questions by writing fresh Python in `run_python` (`backend/agent_tools.py:581`). When a user says:

> "Single earner, £45,000, in Scotland — what's the marginal rate at £60k?"

…then follows up with:

> "What if they were married?"

…the agent must **re-derive the entire household** from scrollback to mutate one field. This is lossy: details silently drift (the country gets dropped, the income changes, the child count resets), the model re-pays tokens to reconstruct context, and comparative conversations — arguably the killer feature of a tax/benefit chatbot — feel brittle.

The proposal: persist a structured **active scenario** (household + reform + comparison baseline) per conversation. The agent reads it at the top of each turn and writes patches to it as the user introduces or modifies parameters, instead of re-extracting everything from text.

---

## 2. Goals / non-goals

### Goals
- Persist a single structured `active_scenario` per conversation, surviving across turns, reloads, and shared links.
- Give the agent two tools: `get_scenario()` (read) and `update_scenario(patch)` (shallow-merge mutate).
- Instruct the agent (via the system prompt) to read the scenario at turn start and patch it whenever the user introduces/modifies household or reform parameters.
- Round-trip the scenario through the existing conversation save/load path (`backend/routes/conversations.py`) so it is durable and shareable.
- Show a subtle "active scenario" pill in the input chrome of `ChatPage.tsx`, with clear/reset.
- Compose cleanly with Plan mode and with the typed reform tools landing in #55 / #94 / #97.

### Non-goals (for v1)
- **Not** building typed `household`/`reform` execution tools — that is #81 / #55 / #97. This layer *persists* structured args; it does not replace `run_python`.
- **Not** multi-scenario / named-scenario management (A vs B vs C side by side). One active scenario per conversation; forking is a future extension.
- **Not** server-authoritative scenario derivation. The agent owns the scenario contents via tool calls; the server only stores and relays them. We do not parse the household from microsim outputs.
- **Not** auto-running simulations when the scenario changes. `update_scenario` records intent; the agent still calls `run_python` to compute.
- **Not** cross-conversation memory or per-user profiles.

---

## 3. Proposed design

### 3.1 The `active_scenario` schema

A free-ish JSON object the agent reads and patches. We keep it deliberately loose for v1 (the agent, not a Pydantic model, is the source of truth), but document a canonical shape so the prompt and the pill renderer agree:

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

The `reform` field intentionally mirrors the structure the typed tools already accept — e.g. `_build_compiled_policy` keys reforms by programme (`income_tax`, `national_insurance`, `universal_credit`, …) in `backend/agent_tools.py:174-188`. That means a persisted `reform` can be handed straight to a future typed tool, or splatted into a `Parameters(...)` call inside `run_python`, with no translation. This is the concrete tie-in to #81/#55: typed args make the scenario *expressible*; this issue makes them *durable*.

Design choice: the schema is **advisory, not validated** server-side in v1. The server stores whatever the agent writes (after a size cap + JSON-shape check). Reasoning in §7.

### 3.2 New tools: `get_scenario` / `update_scenario`

Tools are defined as plain functions in `backend/agent_tools.py`, dispatched through the `tools` dict inside `execute_tool` (`backend/agent_tools.py:688-707`) and declared to Anthropic in the `TOOL_DEFINITIONS` list (`backend/agent_tools.py:710-796`). Adding a tool today means: write the function, add it to the dispatch dict, add a schema entry to `TOOL_DEFINITIONS`.

**The wrinkle:** every existing tool in that dispatch dict is *stateless* — `run_python` and `generate_chart` take only their declared inputs and touch no conversation state. The scenario tools are different: they read/write **per-conversation state**, which `execute_tool` has no handle on today. `execute_tool` is called from the streaming loop via a thread executor with just `(name, input)`:

```python
# backend/routes/chatbot.py:592
result = await loop.run_in_executor(None, execute_tool, tu["name"], tu["input"])
```

So we cannot implement these as pure functions in the existing dispatch dict without threading state through. Two viable approaches:

**Option A — handle the scenario tools in the chat loop, not in `execute_tool`.**
Keep `get_scenario`/`update_scenario` in `TOOL_DEFINITIONS` (so the model sees them and `_tool_defs_for_anthropic` at `backend/routes/chatbot.py:136` forwards them), but intercept them in the loop *before* the `execute_tool` dispatch. The loop already owns a per-request mutable `conversation` list (`backend/routes/chatbot.py:419`); we add a sibling `scenario` dict in the same scope. When a `tool_use` block names `update_scenario`, the loop shallow-merges the patch into `scenario` and returns the new state as the tool_result; `get_scenario` returns the current `scenario`. This keeps `execute_tool` stateless and is the **recommended** approach.

  Sketch (inside the `for tu in tool_uses` result-building region, `backend/routes/chatbot.py:608-629`):
  ```python
  SCENARIO_TOOLS = {"get_scenario", "update_scenario"}
  # ... when collecting tool_uses, split scenario tools out:
  if tu["name"] == "update_scenario":
      scenario = _merge_scenario(scenario, tu["input"].get("patch", {}))
      result = {"status": "ok", "active_scenario": scenario}
  elif tu["name"] == "get_scenario":
      result = {"active_scenario": scenario}
  else:
      result = await loop.run_in_executor(None, execute_tool, tu["name"], tu["input"])
  ```
  `_merge_scenario` lives in `agent_tools.py` (pure, unit-testable): shallow-merge top-level keys, with `household` merged one level deeper so `{"household": {"earners": [...]}}` doesn't clobber `country`/`children`. `patch == {"reform": null}` explicitly clears the reform.

**Option B — pass a mutable context into `execute_tool`.**
Change `execute_tool(name, input, context=None)` and register stateful tools that read/write `context["scenario"]`. More uniform, but it touches the signature every tool and every test (`backend/tests/test_agent_tools.py`) depends on, and the executor call site. Heavier; not recommended for v1.

**`TOOL_DEFINITIONS` entries** (added to the list at `backend/agent_tools.py:710`):

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

> Note on prompt caching: `_tool_defs_for_anthropic` (`backend/routes/chatbot.py:136-150`) stamps `cache_control` on the **last** tool only, so the whole tool array caches as one block. Adding two tools to the end of `TOOL_DEFINITIONS` is fine — the cache breakpoint just moves to the new last tool. We should keep the additions *stable in order* to avoid needless cache invalidation.

### 3.3 System prompt instructions

The system prompt is assembled by `_build_system_blocks` (`backend/routes/chatbot.py:173-196`): a cached `SYSTEM_PROMPT` block, a cached `REFERENCE_DOC` block, then optional **per-turn** directives appended *after* both cache breakpoints (Plan mode at `:192-193`, Charts mode at `:194-195`) so toggling them never invalidates the cache.

We add scenario guidance in two parts:

1. **Static behavioural rules** → appended to the `SYSTEM_PROMPT` constant (`backend/routes/chatbot.py:30-87`), inside the cached block. Roughly:
   > CONVERSATION SCENARIO MEMORY:
   > - A structured `active_scenario` persists across turns. At the start of a turn that touches a household or reform, call `get_scenario()` to recover what the user already specified rather than re-deriving it from the transcript.
   > - Whenever the user introduces or changes any household/reform parameter, call `update_scenario(patch)` with only the changed fields, then compute.
   > - If a change is ambiguous (e.g. "make them richer"), ask a brief clarifying question before patching — do not invent values.
   > - The scenario is advisory context for *you*; every number in your answer must still come from a `run_python` computation, not from the scenario.

2. **Live scenario snapshot** → injected as a **per-turn** block in `_build_system_blocks`, after the cache breakpoints, exactly like the Plan/Charts directives. This means `_build_system_blocks` gains a `scenario: dict | None = None` parameter, and the call site (`backend/routes/chatbot.py:433`) passes the loaded scenario:
   ```python
   if scenario:
       blocks.append({"type": "text",
                      "text": "ACTIVE SCENARIO (current persisted state):\n"
                              + json.dumps(scenario, indent=2)})
   ```
   Injecting the snapshot here (rather than relying solely on `get_scenario()`) means the agent sees the state for free without spending a tool round-trip, while still being able to call `get_scenario()` if it wants the canonical copy. Because it's after the cache breakpoint, a changing scenario never busts the cached prompt/reference. This mirrors the existing per-turn-directive pattern precisely.

### 3.4 Server-side persistence

Conversations persist through `backend/routes/conversations.py`: a SQLModel `ChatConversation` table (`backend/routes/conversations.py:22-32`) backed by `DATABASE_URL` (Postgres — in production this is the Supabase Postgres instance; the issue's "Supabase" maps to this table, distinct from the `supabase` client used only for billing in `backend/routes/billing.py`). Messages are stored as a JSON string in the `messages` column; save is upsert-by-`session_id` in `save_conversation` (`backend/routes/conversations.py:93-131`); load is by id in `get_conversation` (`:153-164`); shared load in `get_shared_conversation` (`:323-337`).

We add one nullable column, `active_scenario` (TEXT, JSON-encoded), to `ChatConversation`. The migration pattern already exists: `ensure_table` (`backend/routes/conversations.py:68-90`) idempotently `ALTER TABLE ... ADD COLUMN` for new columns (see the `share_token`/`user_email` loop at `:75-81`). We extend that loop:

```python
for col, col_type in [("share_token", "TEXT"), ("user_email", "TEXT"),
                      ("active_scenario", "TEXT")]:
```

Then:
- `SaveConversationRequest` (`:48-53`) gains `active_scenario: dict | None = None`; `save_conversation` writes `json.dumps(...)` into the column on both the update (`:103-112`) and insert (`:113-125`) branches.
- `ConversationDetail` (`:64-65`) gains `active_scenario`; `get_conversation` and `get_shared_conversation` deserialize and return it.

**Who owns the canonical scenario at rest?** The chat loop holds the live scenario for the duration of a streamed turn (§3.2). At end of turn, it must be persisted. Two paths, and we should pick deliberately (open question Q1):

- The streamed `done` event (`backend/routes/chatbot.py:547`) already carries per-turn metadata; we add `active_scenario` to it. The frontend stores it in React state and includes it in the next `saveConversation` POST. This keeps the server stateless between requests (consistent with how `messages` already flow: the client owns the transcript and resends it each turn — `backend/routes/chatbot.py:378`, `frontend/src/app/ChatPage.tsx:650`). **Recommended** — it matches the existing client-owns-state architecture.
- Alternatively, the chat route writes the scenario straight to the DB by `session_id`. This makes the server authoritative but introduces a second writer racing the client's `saveConversation` upsert. Avoid for v1.

So the round-trip is: client sends `active_scenario` (if any) in the chat request → loop seeds the live scenario from it → agent patches it → `done` event returns the final scenario → client stores it and POSTs it to `/conversations` alongside `messages`. On reload, `get_conversation` returns it; the client re-seeds it on the next chat request. This requires adding `active_scenario` to `ChatRequest` (`backend/routes/chatbot.py:208-221`) too.

### 3.5 Frontend pill UI

`ChatPage.tsx` already has the right scaffolding:
- Per-conversation client state via `useState` (`frontend/src/app/ChatPage.tsx:260-289`), e.g. `messages`, `planMode`, `chartsMode`.
- A toggle/button row directly under the textarea at `frontend/src/app/ChatPage.tsx:1776-1835` (attach, Plan, Charts pills) — the natural home for an "Active scenario" pill.
- Save/load already structured: `saveConversation` (`:474-514`) builds the POST body at `:504`; `loadConversation` (`:423-447`) hydrates state from `ConversationDetail`; the chat request body is built at `:650`.

Changes:
- New state: `const [activeScenario, setActiveScenario] = useState<ActiveScenario | null>(null);` next to `planMode`/`chartsMode` (`:282-283`).
- Read the scenario off the stream `done` event in the SSE handler (around `:718`, where `data.session_id` is already consumed) → `setActiveScenario(data.active_scenario)`.
- Send it on the next request: add `active_scenario: activeScenario` to the body at `:650`.
- Persist it: add `active_scenario: activeScenario` to the `saveConversation` POST body at `:504`; hydrate it in `loadConversation` from `data.active_scenario` (`:441` region) and clear it in the "new chat" handler (`setMessages([])` at `:547`).
- Render a pill in the toggle row (`:1776`), styled like the Plan/Charts pills (`:1788-1835`), showing a **one-line summary** (e.g. "Single earner, £45k, Scotland") with an `×` to clear → `setActiveScenario(null)` and a `update_scenario({reset:true})`-equivalent on the next turn. Clicking the pill body opens a small modal showing the full JSON structure with a "Reset scenario" button (matches the existing modal pattern used for report/auth dialogs).
- A `summariseScenario(scenario)` helper produces the pill's one-liner from the canonical shape in §3.1.

---

## 4. Data flow — worked example

User turn 1: **"Single earner £45k Scotland — marginal rate at £60k?"**

1. Frontend POSTs `/chat/message` with `messages`, `active_scenario: null` (`frontend/src/app/ChatPage.tsx:650`).
2. Loop seeds `scenario = None`; `_build_system_blocks` injects no snapshot (`backend/routes/chatbot.py:433`). The static rules tell the agent to record new parameters.
3. Agent calls `update_scenario({"patch": {"household": {"earners": [{"employment_income": 45000, "marital_status": "single"}], "children": 0, "country": "Scotland", "year": 2025}}})`. The loop merges it (§3.2) and returns the new state.
4. Agent calls `run_python` to compute the marginal rate at £60k (`backend/agent_tools.py:581`), answers in prose.
5. The `done` event (`backend/routes/chatbot.py:547`) carries `active_scenario`. Frontend `setActiveScenario(...)`, renders the pill "Single earner, £45k, Scotland", and `saveConversation` persists `{messages, active_scenario}` to Postgres (`backend/routes/conversations.py:93`).

User turn 2: **"What if they were married?"**

6. Frontend POSTs again, now with `active_scenario` = the stored object.
7. Loop seeds the live `scenario` from the request; `_build_system_blocks` injects the snapshot block (§3.3) so the agent sees `marital_status: "single"`, £45k, Scotland *without* re-reading scrollback.
8. Agent calls `update_scenario({"patch": {"household": {"earners": [{"employment_income": 45000, "marital_status": "married"}]}}})` — only the changed field, everything else inherited.
9. Agent re-runs `run_python` and answers the married case, optionally contrasting with the single case.
10. `done` returns the updated scenario; pill updates to "Married, £45k, Scotland"; saved again.

The follow-up never restated income, region, year, or children — the whole point of the feature.

---

## 5. Open questions (issue items + recommendations)

**Q1 — Granularity: per-conversation or per-message?**
The issue leans per-conversation with forking. **Recommendation: per-conversation.** One `active_scenario` column on `chat_conversations`, owned by the client and resent each turn (mirrors how `messages` already round-trip). Per-message snapshots would bloat the JSON and complicate the upsert; we can add an explicit "fork scenario" action later (clone the object into a new `session_id`). Sub-question: should we snapshot the scenario *into each saved message* for replay/debugging? Lightweight and useful for the report flow (`backend/routes/conversations.py:196-227` already serializes per-message events) — proposed as a **phase 3** nicety, not v1.

**Q2 — Display: how much in the pill vs the modal?**
**Recommendation: one-line summary in the pill, full JSON in a click-through modal.** Pill = `summariseScenario()` ("Married, £45k, Scotland"); modal = pretty-printed structure + "Reset". This matches the issue's sketch and the existing compact-pill aesthetic of the Plan/Charts buttons (`frontend/src/app/ChatPage.tsx:1788-1835`).

**Q3 — Conflict with Plan mode.**
Plan mode is enforced *structurally*: when `plan_mode` is set, the loop omits `tools` from the request entirely (`backend/routes/chatbot.py:466-467`) so the API cannot emit any `tool_use` — including `update_scenario`. So in Plan mode the agent **cannot** patch the scenario; it can only *read* the injected snapshot (§3.3) and ask clarifying questions. **Recommendation: this is the correct behaviour** — Plan mode is "ask before doing", and "doing" includes mutating state. We should explicitly note in the Plan directive (`backend/routes/chatbot.py:223-230`) that the agent may *reference* the active scenario when forming its clarifying questions but will apply changes on the next (non-plan) turn. No code conflict; just prompt wording.

**Q4 — Relationship to typed tools (#55 / #81 / #97).**
The scenario's `reform` field is deliberately keyed by programme to match `_build_compiled_policy` (`backend/agent_tools.py:174-188`) and the typed `run_economy_simulation` / `calculate_household` signatures (`backend/agent_tools.py:218`, `:374`). **Recommendation: design the scenario schema to be the persistent home for exactly the args those tools accept**, so a persisted scenario can be fed directly to a typed tool once #55/#97 land. This issue is orthogonal-but-complementary: typed tools type the args; this layer persists them. We should land scenario memory *after or alongside* the typed tools so the `reform`/`household` shapes are settled, avoiding a schema redo. **Sequencing note:** today the model only sees `run_python` + `generate_chart` (`backend/agent_tools.py:690-693`, `TOOL_DEFINITIONS` at `:710`); the typed tools exist as functions but are not registered. The system-prompt phrasing for `update_scenario` should not assume typed tools are live until #55/#97 merge.

---

## 6. Phased implementation plan

Each phase is an independently shippable PR.

- **PR 1 — Backend state plumbing (no persistence, no UI).**
  Add `get_scenario`/`update_scenario` to `TOOL_DEFINITIONS` and `_merge_scenario` to `agent_tools.py`; handle them in the chat loop (Option A, §3.2); add the per-turn snapshot block to `_build_system_blocks`; add `active_scenario` to `ChatRequest` and the `done` event. Add the static prompt rules. Unit-test `_merge_scenario` (clear, deep-merge household, reform reset). Behind nothing — the agent simply gains memory within a single streamed session. *Risk: low; fully additive.*

- **PR 2 — Persistence round-trip.**
  Add the `active_scenario` column + migration in `ensure_table`; extend `SaveConversationRequest` / `ConversationDetail` / save/get/shared handlers. Frontend: store the scenario from the `done` event, resend it on each request, include it in `saveConversation`, hydrate it in `loadConversation`, clear on new chat. No visible UI yet beyond persistence. *Risk: low; column is nullable, old rows unaffected.*

- **PR 3 — Pill + modal UI.**
  `summariseScenario` helper, the pill in the input chrome (`:1776` row), the modal, clear/reset wiring. *Risk: low; UI-only.*

- **PR 4 — Polish & edges (optional).**
  Plan-mode directive wording (Q3); per-message scenario snapshot for the report/debug flow (Q1 sub-question); typed-tool integration once #55/#97 land (feed persisted `reform`/`household` straight into typed calls). *Risk: low.*

---

## 7. Risks & alternatives

**Risk: agent forgets to call `update_scenario`.** Memory is only as good as the agent's discipline. Mitigation: inject the live snapshot every turn (§3.3) so even without writing back, the agent always *sees* prior state; reinforce with prompt rules and an eval case (the `single → married` flow) in the eval harness (PR #52 scaffold). The snapshot-on-read is the safety net that makes the feature robust to a missed write.

**Risk: stale or wrong scenario silently corrupts answers.** If the agent patches the scenario incorrectly, later turns inherit the error. Mitigations: every number still comes from a fresh `run_python` (prompt rule), the user-visible pill makes drift *observable* (they can see "Scotland" went missing and reset), and the modal exposes the full state. Observability is the antidote here.

**Risk: unvalidated JSON blob grows / gets malformed.** The scenario is advisory and free-ish. Mitigation: a hard size cap (e.g. 8 KB) and a JSON-shape check in `_merge_scenario`; reject patches that aren't objects. We deliberately *don't* Pydantic-validate in v1 (alternative below).

**Risk: client-owned state can be tampered with.** Since the client resends `active_scenario`, a malicious client could inject arbitrary JSON. But it only flows back into the prompt as advisory text and is never `exec`'d — the sandboxed `run_python` (`backend/agent_tools.py:604-624`) is the only execution path and is unaffected. Acceptable for v1; same trust model as the client already resending `messages`.

**Risk: two writers (client `saveConversation` vs a server-side scenario write).** Avoided by choosing the client-owned path (§3.4, Q1) — single writer, consistent with `messages`.

**Alternative A — server-authoritative scenario, validated by a Pydantic model.**
Tighter and self-documenting, but: (1) it diverges from the current client-owns-transcript architecture and adds a racing DB writer; (2) a rigid schema fights the agent when the user says something the model can't cleanly express, forcing it to drop nuance. Rejected for v1; revisit once the schema stabilizes alongside typed tools (#55/#97), at which point a validated model becomes attractive.

**Alternative B — no tools; derive the scenario from message history with a cheap model each turn.**
We already run a fast model for titles and follow-up suggestions (`backend/routes/chatbot.py:259-326`). We could summarize the household from scrollback the same way. But this re-introduces exactly the lossy re-derivation the issue is trying to kill, costs an extra call per turn, and gives no clean place for the user to *see and edit* state. Rejected.

**Alternative C — store the scenario only in browser localStorage.**
Zero backend change, but breaks the issue's explicit requirement that the scenario survive sharing and cross-device reloads (the conversation already persists server-side; the scenario should travel with it). Rejected.
