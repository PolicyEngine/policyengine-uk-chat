"""
Modal deployment for policyengine-uk-chat.
Deploys the FastAPI backend as a Modal ASGI app.
"""

import modal

app = modal.App("policyengine-uk-chat")


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
        "policyengine-uk-compiled>=0.6.1",
        "policyengine_uk>=2.75.0",
        "pandas",
        "httpx",
        "supabase",
        "stripe",
        "python-dateutil",
    )
    .run_function(_preload_engine)
    .add_local_dir("backend", remote_path="/app/backend", copy=True)
)

chat_secrets = modal.Secret.from_name("policyengine-uk-chat-secrets")


@app.function(
    image=image,
    secrets=[chat_secrets],
    cpu=2.0,
    memory=4096,
    timeout=600,
    max_containers=10,
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
