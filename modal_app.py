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
    # Refresh the CA lists the backend's outbound HTTPS and gRPC calls verify
    # against, so they stop trusting the compromised eMudhra root "emSign ECC
    # Root CA - G3" (https://emudhra.we-have-a-problem.com):
    # - Debian's ca-certificates, read by clients that use the operating-system
    #   store, such as hf-xet for Hugging Face downloads. Modal's debian_slim
    #   base ships 20230311, which includes the root.
    # - certifi, read by httpx and requests (the Anthropic, Supabase and Stripe
    #   clients). requirements.txt does not pin it, so the version is whatever
    #   was current when the step above was last built; 2026.10.19 is the first
    #   release without the root. A minimum, so a rebuild picks up later
    #   removals.
    # - gRPC (Google Cloud Logging) reads the CA list bundled in grpcio, which
    #   no grpcio release has fixed; point it at the refreshed Debian bundle.
    # These steps follow the requirements step so Modal reuses its cached
    # build; changing an earlier step would re-resolve every unpinned package.
    .apt_install("ca-certificates")
    .pip_install("certifi>=2026.10.19")
    .env({"GRPC_DEFAULT_SSL_ROOTS_FILE_PATH": "/etc/ssl/certs/ca-certificates.crt"})
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
    """Adopt an exact legacy baseline when needed, then upgrade to head."""
    import sys

    sys.path.insert(0, "/app/backend")
    os.chdir("/app/backend")

    from persistence.migration_adoption import upgrade_deployed_database

    return upgrade_deployed_database("/app/backend/alembic.ini").debug_summary()


@app.function(
    image=image,
    secrets=[chat_secrets],
    timeout=600,
    region="eu",
)
def inspect_migration_state():
    """Return structural migration state without querying application rows."""
    import sys

    sys.path.insert(0, "/app/backend")
    os.chdir("/app/backend")

    from persistence.database_namespace import (
        configured_database_schema,
        namespaced_engine,
    )
    from persistence.migration_adoption import inspect_database_for_adoption

    database_url = os.environ["ALEMBIC_DATABASE_URL"]
    schema = configured_database_schema()
    engine = namespaced_engine(database_url, schema)
    try:
        summary = inspect_database_for_adoption(
            engine,
            schema=schema,
        ).debug_summary()
        print(summary)
        return summary
    finally:
        engine.dispose()


@app.function(
    image=image,
    secrets=[chat_secrets],
    timeout=600,
    region="eu",
)
def remove_preview_database_schema():
    """Remove only the configured PR-preview PostgreSQL schema."""
    import sys

    sys.path.insert(0, "/app/backend")
    os.chdir("/app/backend")

    from persistence.database_namespace import (
        configured_database_schema,
        drop_preview_schema,
    )

    schema = configured_database_schema()
    if schema is None:
        raise RuntimeError("DATABASE_SCHEMA is required for preview cleanup")
    drop_preview_schema(os.environ["ALEMBIC_DATABASE_URL"], schema)
    return {"removed_schema": schema}


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
