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
    """Bake the engines into the image snapshot for fast cold starts.

    The compiled (Rust) backend is the default and gets the full warm-up.
    The Python backends only need their packages importable — that's enough
    to make `/chat/backends` return without paying for the heavy
    PolicyEngine Core/OpenFisca import on the first request.
    """
    from policyengine_uk_compiled import Simulation
    sim = Simulation(year=2024)
    sim.get_baseline_params()
    print("Compiled engine pre-loaded.")

    # Best-effort imports of the Python backends. Failures are non-fatal —
    # the chat works without them; this is purely to shave cold-start latency
    # off /chat/backends.
    for pkg in ("policyengine_uk", "policyengine_us"):
        try:
            __import__(pkg)
            print(f"{pkg} pre-imported.")
        except ImportError:
            print(f"{pkg} not installed; skipping pre-import.")


image = (
    modal.Image.debian_slim(python_version="3.13")
    .apt_install("libpq-dev", "gcc")
    .pip_install_from_requirements("backend/requirements.txt")
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
