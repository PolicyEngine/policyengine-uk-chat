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
        "sqlalchemy",
        "psycopg2-binary",
        "anthropic",
        "pydantic-ai[anthropic]",
        "policyengine-uk-compiled>=0.3.4",
        "policyengine_uk>=2.75.0",
        "pandas",
        "httpx",
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

    # Map DATABASE_URL env var to the individual DB_* vars the app expects
    database_url = os.environ.get("DATABASE_URL", "")
    if database_url and not os.environ.get("DB_HOST"):
        # Parse postgresql://user:pass@host:port/dbname
        from urllib.parse import urlparse
        u = urlparse(database_url)
        os.environ["DB_HOST"] = u.hostname or "localhost"
        os.environ["DB_PORT"] = str(u.port or 5432)
        os.environ["DB_NAME"] = (u.path or "/microsim").lstrip("/")
        os.environ["DB_USERNAME"] = u.username or "postgres"
        os.environ["DB_PASSWORD"] = u.password or ""

    from main import app as fastapi_app
    return fastapi_app
