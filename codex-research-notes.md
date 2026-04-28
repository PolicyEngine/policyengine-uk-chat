# Codex Research Notes: uk-chat -> app-v2 Integration

Last updated: 2026-04-28

## Repos identified

User-confirmed target repos:

- `policyengine-uk-chat`
  - Local: `/Users/sakshikekre/Work/PolicyEngine/Repos/policyengine-uk-chat`
  - Remote: `https://github.com/PolicyEngine/policyengine-uk-chat.git`
  - Branch inspected locally: `main`
- `policyengine-app-v2`
  - Local: `/Users/sakshikekre/Work/PolicyEngine/Repos/policyengine-app-v2`
  - Remote: `https://github.com/PolicyEngine/policyengine-app-v2.git`
  - Branch inspected locally: `main`

Related model/API repos found during research:

- `policyengine-uk-rust`
  - Local: `/Users/sakshikekre/Work/PolicyEngine/Repos/policyengine-uk-rust`
  - Remote: `https://github.com/PolicyEngine/policyengine-uk-rust.git`
  - Publishes Python package `policyengine-uk-compiled`, import name `policyengine_uk_compiled`
- `policyengine-uk`
  - Local: `/Users/sakshikekre/Work/PolicyEngine/Repos/policyengine-uk`
  - Remote: `https://github.com/PolicyEngine/policyengine-uk.git`
  - Publishes Python package `policyengine-uk`, import name `policyengine_uk`
- `policyengine.py`
  - Local: `/Users/sakshikekre/Work/PolicyEngine/Repos/policyengine.py`
  - Remote checked directly: `https://github.com/PolicyEngine/policyengine.py`
  - Publishes package named `policyengine`
  - Remote repo description from GitHub API: "PolicyEngine's main user-facing Python package, incorporating country packages and integrating data visualization and analytics."
- `policyengine-api`
  - Local: `/Users/sakshikekre/Work/PolicyEngine/Repos/policyengine-api`
  - Remote: `https://github.com/PolicyEngine/policyengine-api.git`
- `policyengine-household-api`
  - Local: `/Users/sakshikekre/Work/PolicyEngine/Repos/policyengine-household-api`
  - Remote: `https://github.com/PolicyEngine/policyengine-household-api.git`
- `policyengine-api-v2`
  - Local: `/Users/sakshikekre/Work/PolicyEngine/Repos/policyengine-api-v2`
  - Remote: `https://github.com/PolicyEngine/policyengine-api-v2.git`

## Remote confirmation: what is `policyengine`?

The `policyengine` package is published from the `PolicyEngine/policyengine.py` repo.

Remote GitHub API check:

```json
{
  "name": "policyengine.py",
  "full_name": "PolicyEngine/policyengine.py",
  "description": "PolicyEngine's main user-facing Python package, incorporating country packages and integrating data visualization and analytics.",
  "html_url": "https://github.com/PolicyEngine/policyengine.py",
  "default_branch": "main"
}
```

Remote `pyproject.toml` on `PolicyEngine/policyengine.py` main showed:

- Project name: `policyengine`
- Remote main version: `4.3.1`
- Description: package to conduct policy analysis using PolicyEngine tax-benefit models
- Optional UK dependency: `policyengine-uk==2.88.0`
- Optional US dependency: `policyengine-us==1.667.1`
- Console script: `policyengine = "policyengine.cli:main"`

Conclusion:

- Yes, `policyengine` is the package from the repo historically named `policyengine.py`.
- It is not the UK country model itself.
- It is a higher-level user-facing package/SDK that incorporates country packages such as `policyengine-uk` and `policyengine-us`.
- API repos may pin older versions. Remote `policyengine-api-v2` simulation project pins `policyengine==0.13.0`, while current `policyengine.py` main is `4.3.1`.

## uk-chat dependencies and stack

Frontend:

- `frontend/package.json`
- Next `^15.0.0`
- React `^19.0.0`
- Mantine `^8.3.8`
- Supabase
- D3
- React Markdown
- Syntax highlighter

Backend:

- `backend/requirements.txt`
- FastAPI
- Uvicorn
- SQLModel
- psycopg2
- Anthropic
- `pydantic-ai[anthropic]`
- `policyengine-uk-compiled>=0.20.0`
- `policyengine_uk`
- pandas/httpx/supabase/stripe

## uk-chat backend control flow

Main app:

- `backend/main.py`
- FastAPI app includes routers:
  - `billing`
  - `chatbot`
  - `conversations`
- Adds CORS and NaN-safe JSON response.
- `/health`
- `/version` reports `policyengine-uk-compiled` package version.

Chat endpoint:

- `backend/routes/chatbot.py`
- Main endpoint: `/chat/message`
- Streams Server-Sent Events to the frontend.
- Selects Anthropic model using env vars:
  - `ANTHROPIC_FAST_MODEL`, default observed as `claude-haiku-4-5`
  - `ANTHROPIC_COMPLEX_MODEL`, default observed as `claude-sonnet-4-6`
- System prompt is tightly coupled to the UK compiled model.
- Prompt says the assistant can use only `run_python`.
- Prompt preloads:
  - `policyengine_uk_compiled as pe`
  - `Simulation`
  - `Parameters`
  - `StructuralReform`
  - `aggregate_microdata`
  - `combine_microdata`
  - `capabilities`
  - `ensure_dataset`
  - `pd`
  - `np`
  - `json`
  - `math`

SSE event flow in `/chat/message`:

- Frontend sends messages to backend.
- Backend calls Anthropic streaming API.
- Backend streams content chunks as `chunk`.
- Tool invocation starts as `tool_start`.
- Tool call payload as `tool_use`.
- Backend executes tool via `execute_tool` in an executor.
- Tool output as `tool_result`.
- Final event as `done`.
- Error event as `error`.

Tool implementation:

- `backend/agent_tools.py`
- `_ensure_compiled_package_importable()` tries to import `policyengine_uk_compiled`.
- If import fails, it falls back to local `../policyengine-uk-rust/interfaces/python`.
- Only exported Anthropic tool is currently `run_python`.
- `run_python(code)`:
  - Imports `policyengine_uk_compiled as pe`.
  - Preloads compiled engine symbols into `allowed_globals`.
  - Captures stdout from `print()`.
  - Runs `exec(code, allowed_globals)`.
  - Returns JSON-safe `result` plus captured output.
- `TOOL_DEFINITIONS` describes this as the "official PolicyEngine UK compiled interface."

Important implication:

- uk-chat is not abstracted over model backends today.
- The LLM-facing tool contract, prompt, imports, and examples are all coupled to `policyengine_uk_compiled`.
- The likely abstraction boundary is around:
  - model/backend adapter capability metadata,
  - system prompt/tool documentation,
  - safe execution globals,
  - normalized result/chart conventions,
  - possibly a generic `run_model_code` or backend-specific tool namespace.

## uk-chat frontend control flow

Main chat page:

- `frontend/src/app/ChatPage.tsx`
- Posts to `getBackendEndpoint("chat/message")`.
- Reads `response.body.getReader()`.
- Parses lines prefixed with `data: ...`.
- Handles event types:
  - `chunk`
  - `tool_start`
  - `tool_use`
  - `tool_result`
  - `done`
  - `error`
- Special-cases `run_python`:
  - Shows Python code.
  - Shows output/result in a collapsible working section.
- Saves and loads conversations through `/conversations`.
- Generates titles through `/chat/title`.
- Uses billing endpoints for credits/subscription state.

Proxy:

- `frontend/src/app/api/proxy/[...slug]/route.ts`
- Proxies frontend calls to backend.
- Uses `BACKEND_URL`; default local backend was `localhost:8080`.
- Preserves SSE streaming.

Charts:

- `frontend/src/components/charts/index.tsx`
- Parses fenced markdown blocks like:

````
```chart
{ ...json chart spec... }
```
````

Conversation/billing:

- `backend/routes/conversations.py`
  - SQLModel table `chat_conversations`
  - share/report routes
- `backend/routes/billing.py`
  - Supabase credits
  - Stripe
  - Anthropic token cost accounting

## policyengine-uk-rust / `policyengine_uk_compiled`

Repo README says it is a high-performance UK tax-benefit microsimulation engine in Rust with Python wrapper `policyengine-uk-compiled`.

Python package:

- Distribution name: `policyengine-uk-compiled`
- Import name: `policyengine_uk_compiled`
- Local version observed: `0.20.0`

Exports from `interfaces/python/policyengine_uk_compiled/__init__.py`:

- `Simulation`
- `StructuralReform`
- `Parameters`
- `aggregate_microdata`
- `combine_microdata`
- `capabilities`
- `ensure_dataset`
- default helpers
- pydantic models

Engine flow:

- `interfaces/python/policyengine_uk_compiled/engine.py`
- `Simulation.__init__` accepts:
  - year
  - pandas DataFrames or CSV strings
  - `data_dir`
  - `dataset`
  - `clean_frs_base`
  - `clean_frs`
  - `frs_raw`
  - `binary_path`
- Builds stdin payload from DataFrames or CLI args.
- `_build_cmd` creates command:
  - binary
  - `--year`
  - `--stdin-data` or `--data`
  - `--policy-json`
  - optional `--persons-only` for SPI
- `run()` shells out with `subprocess.run`, parses JSON into `SimulationResult`.
- `run_microdata()` shells out with `--output-microdata-stdout`, parses CSV output into `MicrodataResult`.
- `models.py` has pydantic parameter/output models mirroring Rust structures.
- `data.py` handles dataset download/cache and `POLICYENGINE_UK_DATA_TOKEN`.
- `structural.py` has `StructuralReform(pre, post)` and Python-side aggregation.

## `policyengine-uk` Python country package

Package:

- Distribution: `policyengine-uk`
- Import: `policyengine_uk`
- Local version observed: `2.88.9`
- Remote `policyengine.py` optional UK dependency pins `policyengine-uk==2.88.0`

Purpose:

- UK microsimulation model based on PolicyEngine Core/OpenFisca style.
- More detailed/enhanced Python calculation package compared with the Rust compiled package in uk-chat.

Important exports from `policyengine_uk/__init__.py`:

- `CountryTaxBenefitSystem`
- `Microsimulation`
- `Simulation`
- parameters/variables

Core flow:

- `policyengine_uk/simulation.py`
  - `Simulation(CoreSimulation)`
  - Can build from situation, dataset, DataFrame, URL.
  - Applies scenario/reform.
  - Has baseline clone.
  - Uses inherited/extended `calculate`.
- `policyengine_uk/microsimulation.py`
  - Adds weighted `calculate`.
  - Returns `MicroSeries`/`MicroDataFrame` style outputs.
- `policyengine_uk/tax_benefit_system.py`
  - Loads variables/parameters.
  - Processes parameters.

## app-v2 structure and flow

Monorepo:

- Root package: `policyengine-monorepo`
- Uses Bun workspaces:
  - `packages/*`
  - `app`
  - `website`
  - `calculator-app`

Major packages:

- `app`
  - Vite React 19 calculator app
  - Redux Toolkit
  - React Query
  - React Router
  - Plotly/Recharts
  - Tailwind
- `calculator-app`
  - Next 15 wrapper for calculator
- `website`
  - Next 15 website
- `packages/design-system`
  - shared design tokens/components

Routing:

- `app/src/CalculatorRouter.tsx`
- Routes under `/:countryId`
- Includes:
  - report output
  - simulation creation
  - household creation
  - policy creation
  - list pages
  - reports

Creation flow:

- `SimulationSubmitView.tsx`
- Uses `useCreateSimulation`.
- Uses `SimulationAdapter.toCreationPayload`.
- Payload contains:
  - `population_id`
  - `population_type`
  - `policy_id`
- Creation does not itself run model calculations.

API constants:

- `app/src/constants.ts`
- `BASE_URL = 'https://api.policyengine.org'`
- `CURRENT_YEAR = '2026'`

Current calculation flow in app-v2:

- `app/src/libs/calculations/CalcOrchestrator.ts`
- Builds metadata/params.
- Chooses strategy:
  - `household` if `populationType === 'household'`
  - otherwise `societyWide`
- Uses React Query query infrastructure.
- Persists through `ResultPersister`.

Household strategy:

- `app/src/libs/calculations/HouseholdCalcStrategy.ts`
- Calls `fetchHouseholdCalculation(country, populationId, policyId)` once.
- API call:
  - `app/src/api/householdCalculation.ts`
  - `GET ${BASE_URL}/${countryId}/household/${householdId}/policy/${policyId}`
  - Four-minute abort timeout.
  - Returns `HouseholdData`.

Society-wide strategy:

- `app/src/libs/calculations/SocietyWideCalcStrategy.ts`
- Calls `fetchSocietyWideCalculation`.
- API call:
  - `app/src/api/societyWideCalculation.ts`
  - `GET ${BASE_URL}/${countryId}/economy/${reformPolicyId}/over/${baselinePolicyId}?region=...&time_period=...`
- Response statuses:
  - `computing`
  - `ok`
  - `error`
- Strategy polls while pending.

Persistence:

- `app/src/libs/calculations/ResultPersister.ts`
- Persists result to report or simulation.
- Household report aggregates simulation outputs to report when all complete.

Existing v2 API client modules:

- `app/src/api/v2/householdCalculation.ts`
  - async job endpoints `/household/calculate`
  - statuses `PENDING|RUNNING|COMPLETED|FAILED`
  - supports US/UK payloads
- `app/src/api/v2/economyAnalysis.ts`
  - `/analysis/economic-impact`
  - `/analysis/economy-custom`
  - async polling
- `app/src/api/v2/taxBenefitModels.ts`
  - `API_V2_BASE_URL = process.env.NEXT_PUBLIC_API_V2_URL || 'https://v2.api.policyengine.org'`
  - model names include `policyengine-us`, `policyengine-uk`

Important implication:

- app-v2 currently creates policy/population/simulation records separately from calculations.
- Current core calculation strategies call the older v1 API by default.
- There are v2 API modules present but they do not appear to be the main active orchestrator path for existing calculator flows.

## API repos and package pins

`policyengine-api` local checkout:

- `setup.py` locally showed:
  - `policyengine_uk==2.39.0`
  - `policyengine_us==1.499.0`
  - `policyengine_core>=3.16.6`
  - `policyengine>=0.7.0`
- `policyengine_api/country.py`
  - dynamically imports country packages.
  - builds metadata from `CountryTaxBenefitSystem` and `Simulation`.
- `policyengine_api/routes/economy_routes.py`
  - route `/<country_id>/economy/<policy_id>/over/<baseline_policy_id>`
  - parses `region`, `dataset`, `time_period`
  - calls `EconomyService`
- `policyengine_api/services/economy_service.py`
  - gets policy JSON.
  - builds `SimulationOptions` from `policyengine.simulation`.
  - sends to simulation API on GCP/Modal.
- `policyengine_api/libs/simulation_api_modal.py`
  - calls Modal `/simulate/economy/comparison`.

`policyengine-household-api` local checkout:

- `setup.py` locally showed:
  - `policyengine_uk==2.31.0`
  - `policyengine_us==1.663.0`
- `policyengine_household_api/country.py`
  - `PolicyEngineCountry.calculate()`
  - clones/prepares tax-benefit system.
  - applies parameter updates via `get_parameter`.
  - instantiates `country_package.Simulation(tax_benefit_system=system, situation=household)`.
  - calls `simulation.calculate(variable_name, period)`.

`policyengine-api-v2` remote check:

- Remote file checked:
  - `projects/policyengine-api-simulation/pyproject.toml`
- Dependencies on remote main included:
  - `policyengine==0.13.0`
  - `policyengine-core>=3.23.5`
  - `policyengine-uk==2.88.0`
  - `policyengine-us==1.653.3`
  - `modal>=0.73.0`
  - Python `>=3.13,<3.14`
- Simulation endpoint imports:
  - `policyengine.simulation.SimulationOptions`
  - `policyengine.simulation.Simulation`
- Endpoint:
  - `/simulate/economy/comparison`
  - calls `Simulation(**model.model_dump()).calculate_economy_comparison()`

## Integration implications to revisit

Likely phases for integrating uk-chat interface into app-v2:

1. Treat chat UI as a frontend product surface.
   - Decide whether it lives inside `app`, `calculator-app`, or as embedded route/module.
   - Preserve streaming/SSE behavior.
   - Map auth/billing/conversation storage to app-v2 conventions if needed.

2. Extract/abstract uk-chat backend model adapter.
   - Current hard couplings:
     - system prompt names `policyengine_uk_compiled`
     - tool definition names compiled engine
     - execution globals import compiled package
     - examples and capability assumptions are compiled-specific
   - Candidate abstraction:
     - `ModelBackend` interface with:
       - `id`
       - `displayName`
       - `prompt_context`
       - `tool_definitions`
       - `execution_globals`
       - `capabilities`
       - `examples`
       - result serialization helpers

3. Implement a Python `policyengine_uk` backend adapter.
   - Should expose `policyengine_uk`, `CountryTaxBenefitSystem`, `Simulation`, `Microsimulation`, and standard helpers.
   - Needs careful examples because the API shape differs from `policyengine_uk_compiled`.
   - Values may differ from compiled Rust engine because compiled package appears associated with an older/smaller model surface.

4. Decide whether chat tool should execute arbitrary Python or call narrower backend operations.
   - Existing arbitrary `run_python` gives maximum flexibility but makes backend abstraction leaky.
   - Narrower model operations would be safer and easier to port, but would reduce LLM flexibility unless designed well.

5. Normalize output conventions.
   - Current frontend expects streaming text plus tool code/result/output.
   - Charts are carried via markdown fenced `chart` JSON.
   - Any backend adapter should preserve these conventions so frontend stays simple.

## Repo role map: `policyengine-uk`, `policyengine-uk-data`, `policyengine-core`, `policyengine.py`

Remote `PolicyEngine/policyengine-uk-data` was checked directly:

- Repo: `https://github.com/PolicyEngine/policyengine-uk-data`
- Description: "Repository creating UK microdata."
- Package name in remote `pyproject.toml`: `policyengine_uk_data`
- Remote version observed: `1.54.0`
- Purpose: build and publish UK microdata HDF5 files, not run the tax-benefit formulas at request time.
- Top-level package folders include:
  - `policyengine_uk_data/datasets`
  - `policyengine_uk_data/calibration`
  - `policyengine_uk_data/parameters`
  - `policyengine_uk_data/storage`
  - `policyengine_uk_data/targets`
  - `policyengine_uk_data/utils`
- Remote `Makefile` targets include:
  - `data`: runs `policyengine_uk_data/datasets/create_datasets.py`
  - `download`: downloads private prerequisites
  - `upload`: uploads completed datasets
- `create_datasets.py` creates base FRS data, applies imputations, uprates, calibrates local-area/constituency weights, and saves files such as:
  - `frs_2023_24.h5`
  - `enhanced_frs_2023_24.h5`
  - tiny variants for testing

Runtime role distinction:

- `policyengine-uk-data`
  - Data factory / ETL / calibration repo.
  - Produces HDF5 datasets in the Hugging Face namespace used by runtime code:
    - `hf://policyengine/policyengine-uk-data/frs_2023_24.h5`
    - `hf://policyengine/policyengine-uk-data/enhanced_frs_2023_24.h5`
  - Depends on `policyengine-uk` because data construction can use model schemas/classes and needs model-compatible variables/entities.
  - It is not imported in the main runtime calculation path in app-v2/API v2.

- `policyengine-uk`
  - UK model/law package.
  - Distribution: `policyengine-uk`; import: `policyengine_uk`.
  - Owns UK entities, variables, YAML parameters, policy formulas, UK-specific simulation setup, and microsimulation wrappers.
  - Depends on `policyengine-core`.
  - Consumes HDF5 data from `policyengine-uk-data` via Hugging Face URLs or in-memory `UKSingleYearDataset` / `UKMultiYearDataset`.

- `policyengine-core`
  - Generic OpenFisca-style calculation engine.
  - Owns `TaxBenefitSystem`, `Simulation`, entities/populations, holders/caches, parameter trees, variable loading, reform application, formula resolution, period handling, entity mapping, and tracing.
  - Does not know UK policy substance.
  - `policyengine-uk` subclasses/configures it.

- `policyengine.py` / package `policyengine`
  - Higher-level product/API SDK.
  - Repo name: `policyengine.py`; package name: `policyengine`.
  - Wraps country models into stable objects such as `PolicyEngineUKLatest`, `PolicyEngineUKDataset`, generic `Simulation`, outputs/analysis helpers, dataset serialization, and API-facing model/version metadata.
  - It imports `policyengine_uk` and calls into `policyengine_uk.Microsimulation`.
  - API v2 simulation project pins `policyengine==0.13.0`, while current remote `policyengine.py` main was observed at `4.3.1`.

Concrete data/control path for an economy-style UK simulation:

1. Offline data build:
   - `policyengine-uk-data` reads raw/private FRS and supporting data.
   - It creates a base FRS dataset.
   - It imputes wealth, consumption, VAT, public services, income, capital gains, salary sacrifice, student-loan plan, etc.
   - It clones/assigns local geography and calibrates weights.
   - It saves HDF5 outputs and uploads/publishes them.

2. Runtime dataset load:
   - `policyengine.py` exposes `PolicyEngineUKDataset`, pointing at an HDF5 filepath or generated local file.
   - Or `policyengine-uk` directly receives a Hugging Face URL like `hf://policyengine/policyengine-uk-data/enhanced_frs_2023_24.h5`.
   - `policyengine-uk` downloads through `policyengine_core.tools.hugging_face.download_huggingface_dataset`.
   - It wraps HDF5 tables into `UKSingleYearDataset` or `UKMultiYearDataset`.

3. Runtime model setup:
   - `policyengine-uk` creates `CountryTaxBenefitSystem`.
   - That loads UK entities from `policyengine_uk/entities.py`.
   - It loads variables from `policyengine_uk/variables`.
   - It loads parameters from `policyengine_uk/parameters`.
   - It runs UK-specific parameter processing: private-pension uprating, lagged earnings/CPI, triple lock, economic assumptions, propagation/uprating/backdating/fiscal-year conversion.

4. Runtime simulation execution:
   - `policyengine.py` `Simulation.ensure()` calls the model version's `run()`.
   - For UK, `PolicyEngineUKLatest.run()` creates a `policyengine_uk.Microsimulation(dataset=input_data)`.
   - It applies policy/dynamic parameter modifiers if provided.
   - It loops over selected output variables and calls `microsim.calculate(var, period, map_to=entity)`.

5. Core calculation:
   - `policyengine_uk.Microsimulation.calculate()` delegates to `policyengine_core.simulations.Simulation.calculate()`.
   - Core resolves the variable, entity population, period, caches, and dependencies.
   - Core calls `_run_formula()`.
   - `_run_formula()` either:
     - executes the variable's Python `formula(population, period, parameters)`, or
     - resolves declarative `adds` / `subtracts`.
   - Example: `household_net_income` in `policyengine-uk` has `adds = ["household_market_income", "household_benefits"]` and `subtracts = ["household_tax", "pension_contributions"]`; core recursively calculates those components and combines them.
   - `Microsimulation.calculate()` wraps top-level outputs as `MicroSeries` with survey weights.

6. Output persistence/return:
   - `policyengine.py` converts calculated arrays into entity-level `MicroDataFrame`s.
   - It writes a `PolicyEngineUKDataset` output HDF5 for the simulation.
   - API/app layers consume those output datasets or aggregate them into impact outputs.

