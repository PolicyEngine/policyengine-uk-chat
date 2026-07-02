# The engine layer

`backend/engine/` holds the deterministic helpers that sit between the chat
tools and the compiled `policyengine-uk-compiled` package. These modules
normalise inputs, run simulations, build reforms, query microdata, and serialise
results into the shapes the model expects — keeping the tool layer thin and the
calculation boundary explicit.

```{important}
The "engine" here is the Rust-backed **compiled** package
(`policyengine-uk-compiled`), not the pure-Python `policyengine-uk`. All chat
calculations run through the compiled package; code paths that reach for the
pure-Python engine are out of scope for this pathway.
```

## Module map

```{list-table}
:header-rows: 1
:widths: 30 70

* - Module
  - Purpose
* - `sandbox.py`
  - Restricted Python execution helpers used by chat tools. Backs the
    `run_python` tool — see [Tools](tools.md).
* - `simulations.py`
  - PolicyEngine UK compiled-package and simulation helpers.
* - `reforms.py`
  - Parametric reform validation and compiled-policy construction. Reforms are
    keyed by programme (e.g. `income_tax`, `national_insurance`,
    `universal_credit`).
* - `households.py`
  - Illustrative household input normalization. Backs `calculate_household`.
* - `microdata.py`
  - Microdata loading, filtering, and aggregate operations. Backs
    `analyse_microdata`.
* - `lookups.py`
  - Compatibility facade for deterministic metadata lookup helpers.
* - `serialization.py`
  - Serialization helpers for tool outputs.
* - `reference.py`
  - Generate `reference.md` from the installed `policyengine_uk_compiled`
    library.
```

## Generated reference documents

`backend/engine/reference.py` generates **two** documents at build time, run
against the installed engine so they always describe the exact version that will
execute:

```{list-table}
:header-rows: 1
:widths: 35 65

* - File
  - Contents
* - `backend/reference.md`
  - The full API reference — an engine-capabilities snapshot, the public API
    docs, reform recipes, and the `Parameters` JSON schema. Injected into the
    compute agent's context so it can write correct code without guessing.
* - `backend/scope_descriptor.md`
  - A compact descriptor (built via `build_scope_descriptor()`) listing the
    modelled programmes, datasets, years, and the "not modelled" boundary. Used
    by the lightweight gateway prompts where the full reference would be too
    heavy.
```

Both files are git-ignored and regenerated (`python engine/reference.py`) in
**both** the Docker image build and the Modal image build, so the deployed agent
always reads a reference that matches the engine it runs against. See
[Deployment](../deployment.md) and the [Backend overview](overview.md) for how
the build wiring works.

```{note}
`GET /version` reports the `policyengine_uk_compiled` version the deployed
reference documents are stamped to — the quickest way to confirm a deployment's
engine build.
```

## Parameter lookup

The `lookup_parameter` tool is backed by the `backend/engine/lookup/` package,
which resolves baseline parameter values **without running a simulation**:

```{list-table}
:header-rows: 1
:widths: 35 65

* - Module
  - Purpose
* - `lookup/parameters.py`
  - `lookup_parameter_metadata(parameters, query, year, limit)` looks up a
    baseline parameter by exact dotted path (e.g.
    `income_tax.personal_allowance`) **or** a natural-language query (e.g.
    "personal allowance").
* - `lookup/scoring.py`
  - Deterministic string-similarity (F1-style) matching of the query against
    parameter names, labels, and hand-authored aliases.
```

A hand-maintained `_PARAMETER_ALIASES` map in `parameters.py` maps canonical
parameter paths to natural-language synonyms, improving recall for everyday
phrasings.

```{important}
Parameter lookup is **deterministic** — there is no model call. It is a cheap,
reliable way to fetch a static parameter value when the answer does not require
a simulation. See [Tools](tools.md).
```

## The compiled-output contract

The engine layer is where the "compiled-output contract" lives: how
compiled-engine results are normalised and serialised (`serialization.py`)
before being handed back to the model as tool results. Centralising that
contract here keeps tool outputs consistent and shields the agent from the raw
shapes the compiled package emits.
