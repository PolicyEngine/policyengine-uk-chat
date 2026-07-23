# Deployment

The backend can run in Docker or as the Modal ASGI app in `modal_app.py`. Both
install `backend/requirements.txt`, whose exact `policyengine[uk]` pin keeps the
policyengine.py runtime and its certified UK country package together.

## Modal

The Modal image uses Python 3.13, installs system build dependencies and Python
requirements, imports `policyengine.py`, and resolves the default Enhanced FRS
logical dataset name at build time. Dataset contents are materialized at
runtime by `pe.uk.ensure_datasets`.

The web function runs FastAPI with the configured Modal secret, two CPUs, 4 GB
memory, and a 600-second timeout. `POLICYENGINE_DATA_FOLDER` controls the local
dataset cache location.

## Docker

`backend/Dockerfile` installs the same requirements and serves the FastAPI app.
Set the environment variables documented in `.env.example`, including Anthropic,
Supabase, billing, and allowed-host settings. Access to a restricted Enhanced
FRS source must also be available in the deployment environment.

## Version checks

`GET /version` reports:

```json
{
  "engine": "policyengine.py",
  "engine_version": "<policyengine version>",
  "policyengine_uk": "<policyengine-uk version>"
}
```

Dependency update automation must update the runtime pin, tests, and deployed
image together. Dataset names are resolved through the policyengine.py release
manifest rather than a generated in-repo engine reference file.

## Health and observability

Use `GET /health` for liveness. FastAPI and Modal process metadata are configured
through `policyengine-observability`; deployment failures should be checked in
the platform logs and the configured telemetry backend.
