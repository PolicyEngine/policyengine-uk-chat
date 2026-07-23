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
    """Import policyengine.py and resolve the default UK dataset at build time."""
    import policyengine as pe
    from policyengine.provenance.manifest import resolve_dataset_reference

    pe.uk.model
    resolve_dataset_reference("uk", "enhanced_frs_2023_24")
    print("policyengine.py UK engine pre-loaded.")


image = (
    modal.Image.debian_slim(python_version="3.13")
    .apt_install("libpq-dev", "gcc")
    .pip_install_from_requirements("backend/requirements.txt")
    .run_function(_preload_engine)
    .add_local_dir("backend", remote_path="/app/backend", copy=True)
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

    sys.path.insert(0, "/app/backend")
    os.chdir("/app/backend")

    from observability.fastapi import configure_process_observability

    configure_process_observability(
        platform="modal",
        service_role="api",
        runtime_role="modal_web",
        modal_app_name=APP_NAME,
        modal_function_name="web",
    )

    from api.main import app as fastapi_app
    return fastapi_app
