# Engine

The backend calculation boundary is `policyengine.py` plus the UK country
package. The pinned runtime dependencies are in `backend/requirements.txt`.

## Runtime boundary

`backend/engine/py_runtime.py` owns package access:

- resolves dataset names through the policyengine.py release manifest;
- materializes a certified dataset/year with `pe.uk.ensure_datasets`;
- creates baseline and reform `policyengine.core.Simulation` objects;
- runs the UK synthetic-household calculator; and
- exposes model metadata for discovery.

`backend/engine/simulations.py` stores a baseline/reform pair in
`SocietySimulationRun`. It returns only metadata to the model. The actual
simulation objects remain in the turn-local tool result store. Output datasets
are run in memory rather than persisted into policyengine.py's process cache.

## Datasets

UK Chat defaults to `enhanced_frs_2023_24`. The logical name is resolved to the
URI certified by the installed policyengine.py release manifest. This preserves
the app's Enhanced FRS workflow while using policyengine.py provenance and
materialization.

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
`backend/engine/axes.py` runs one numeric household sweep and retains only the
requested numeric outputs. It reads the actual axis coordinates from the
calculator result rather than rebuilding the grid independently. Named
contracts in `backend/engine/axes_schemas.py` define every required axes input,
stored value, successful result, and bounded-result error field.

## Society outputs

`backend/engine/derivatives.py` is a thin adapter over official policyengine.py
output classes. Weighted aggregation belongs to those classes. Runtime code
must not derive society totals from raw arrays or implement weights locally.

The serialization layer rejects pandas tabular objects. This is both a privacy
boundary and a guard against bypassing the output classes.
