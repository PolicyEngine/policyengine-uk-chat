"""
Modal deployment for policyengine-uk-chat.
Deploys the FastAPI backend as a Modal ASGI app.
"""

import os

import modal


APP_NAME = os.environ.get("POLICYENGINE_UK_CHAT_MODAL_APP_NAME", "policyengine-uk-chat")
SECRET_NAME = os.environ.get("POLICYENGINE_UK_CHAT_MODAL_SECRET_NAME", "policyengine-uk-chat-secrets")

app = modal.App(APP_NAME)


def _preload_engine():
    """Bake the compiled engine into the image snapshot for fast cold starts."""
    from policyengine_uk_compiled import Simulation
    sim = Simulation(year=2024)
    sim.get_baseline_params()
    print("Engine pre-loaded.")


image = (
    modal.Image.debian_slim(python_version="3.13")
    .apt_install("libpq-dev", "gcc")
    .pip_install(
        "fastapi",
        "uvicorn[standard]",
        "sqlmodel",
        "psycopg2-binary",
        "anthropic",
        "pydantic-ai[anthropic]",
        "policyengine-uk-compiled>=0.20.0",
        "policyengine_uk>=2.75.0",
        "pandas",
        "httpx",
        "supabase",
        "stripe",
        "python-dateutil",
    )
    .run_function(_preload_engine)
    .add_local_dir("backend", remote_path="/app/backend", copy=True)
    # Regenerate reference.md against the Modal-installed
    # policyengine-uk-compiled version so the deployed backend always serves a
    # fresh API reference. This mirrors the equivalent step in backend/Dockerfile.
    .run_commands("cd /app/backend && python scripts/build_reference.py")
)

chat_secrets = modal.Secret.from_name(SECRET_NAME)


@app.function(
    image=image,
    secrets=[chat_secrets],
    cpu=2.0,
    memory=4096,
    timeout=600,
    max_containers=10,
    region="eu",
)
@modal.concurrent(max_inputs=100)
@modal.asgi_app()
def web():
    import sys
    import os

    sys.path.insert(0, "/app/backend")
    os.chdir("/app/backend")

    from main import app as fastapi_app
    return fastapi_app
