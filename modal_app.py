"""
Modal deployment for policyengine-uk-chat.
Deploys the FastAPI backend as a Modal ASGI app.
"""

import os

import modal


APP_NAME = os.environ.get("POLICYENGINE_UK_CHAT_MODAL_APP_NAME", "policyengine-uk-chat")
SECRET_NAME = os.environ.get("POLICYENGINE_UK_CHAT_MODAL_SECRET_NAME", "policyengine-uk-chat-secrets")
WEB_MEMORY_MIB = 16_384

app = modal.App(APP_NAME)

image = (
    modal.Image.debian_slim(python_version="3.13")
    .apt_install("libpq-dev", "gcc")
    .pip_install_from_requirements("backend/requirements.txt")
    .add_local_dir("backend", remote_path="/app/backend", copy=True)
)

chat_secrets = modal.Secret.from_name(SECRET_NAME)


@app.function(
    image=image,
    secrets=[chat_secrets],
    timeout=600,
    region="eu",
)
def migrate():
    """Upgrade the configured PostgreSQL database before API deployment."""
    import sys

    sys.path.insert(0, "/app/backend")
    os.chdir("/app/backend")

    from alembic import command
    from alembic.config import Config

    command.upgrade(Config("/app/backend/alembic.ini"), "head")


@app.function(
    image=image,
    secrets=[chat_secrets],
    cpu=2.0,
    memory=WEB_MEMORY_MIB,
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
