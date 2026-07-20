"""
FastAPI entrypoint for the microsim public chatbot.
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from policyengine_observability import shutdown_observability
from slowapi.errors import RateLimitExceeded

import billing
import chat
import conversations
from api.errors import NaNSafeJSONResponse, rate_limit_handler
from api.rate_limit import limiter
from observability.fastapi import init_observability

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_hostnames_env = os.environ.get("HOSTNAMES", "")
if _hostnames_env:
    HOSTNAMES = _hostnames_env.split(",")
else:
    # Fail closed: an unset HOSTNAMES must not silently allow every origin,
    # which combined with allow_credentials=True would expose the API to any
    # website. Deploy and docker-compose set HOSTNAMES explicitly.
    HOSTNAMES = []
    logger.warning(
        "HOSTNAMES is not set; blocking all cross-origin requests. "
        "Set HOSTNAMES to a comma-separated list of allowed origins."
    )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    conversations.ensure_table()
    yield
    shutdown_observability()


app = FastAPI(
    title="Microsim Public Chatbot API",
    version="1.0.0",
    default_response_class=NaNSafeJSONResponse,
    lifespan=lifespan,
)

app.state.limiter = limiter


app.add_exception_handler(RateLimitExceeded, rate_limit_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=HOSTNAMES,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(billing.router)
app.include_router(chat.router)
app.include_router(conversations.router)

init_observability(app, service_role="api")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/version")
def version():
    from importlib.metadata import version as pkg_version

    def _pkg_version(name: str) -> str:
        try:
            return pkg_version(name)
        except Exception:
            return "unknown"

    # TEMPORARY: while the Python engine override is active (see
    # prompts/system.py TEMPORARY_PYTHON_ENGINE_OVERRIDE), calculations run
    # on policyengine.py, so the badge reports that stack. Restore
    # engine/engine_version to the compiled package when reverting.
    return {
        "engine": "policyengine.py",
        "engine_version": _pkg_version("policyengine"),
        "policyengine_uk": _pkg_version("policyengine-uk"),
        "policyengine_uk_compiled": _pkg_version("policyengine-uk-compiled"),
    }
