# Engine

The backend calculation boundary is `policyengine.py` plus the UK country
package. The pinned runtime dependencies are in `backend/requirements.txt`.

## Runtime boundary

`backend/engine/py_runtime.py` owns package access:

- resolves the pinned default URI and named alternatives;
- materializes the configured dataset/year with `pe.uk.ensure_datasets`;
- creates baseline and reform `policyengine.core.Simulation` objects;
- runs the UK synthetic-household calculator; and
- exposes model metadata for discovery.

`backend/engine/simulations.py` stores a baseline/reform pair in
`SocietySimulationRun`. It returns only metadata to the model. The actual
simulation objects remain in the turn-local tool result store. Output datasets
are run in memory rather than persisted into policyengine.py's process cache.

## Datasets

UK Chat defaults to `enhanced_frs_2024_25`. The logical name maps to the pinned
PolicyEngine UK data release in `backend/engine/constants.py`, with
`POLICYENGINE_UK_DEFAULT_DATASET` available as a deployment override. The
resolved URI is then materialized through policyengine.py. This direct Enhanced
FRS reference is intentional; UK Chat does not use the certified Populace
dataset as its default.

The standard policyengine.py UK default is `populace_uk_2023`; the constant and
discovery metadata retain that option so the chat default can be switched to the
standard bundle default if needed. `frs_2023_24` is also exposed.

Datasets are cached under `POLICYENGINE_DATA_FOLDER`, defaulting to
`/tmp/policyengine-uk-chat-data`.

## Reforms and households

Reforms use flat policyengine.py parameter paths, for example:

```json
{
  "gov.hmrc.income_tax.allowances.personal_allowance.amount": 15000
}
```

`backend/engine/reforms.py` validates and compiles these reforms through the
policyengine.py reform API. `backend/engine/households.py` validates synthetic
household inputs and calls the managed UK household calculator.

## Society outputs

`backend/engine/derivatives.py` is a thin adapter over official policyengine.py
output classes. Weighted aggregation belongs to those classes. Runtime code
must not derive society totals from raw arrays or implement weights locally.

The serialization layer rejects pandas tabular objects. This is both a privacy
boundary and a guard against bypassing the output classes.
