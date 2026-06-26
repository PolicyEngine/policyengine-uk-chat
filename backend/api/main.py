"""
FastAPI entrypoint for the microsim public chatbot.
"""

import logging
import os

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
HOSTNAMES = _hostnames_env.split(",") if _hostnames_env else ["*"]


app = FastAPI(
    title="Microsim Public Chatbot API",
    version="1.0.0",
    default_response_class=NaNSafeJSONResponse,
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


@app.on_event("startup")
def startup():
    conversations.ensure_table()


@app.on_event("shutdown")
def shutdown():
    shutdown_observability()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/version")
def version():
    try:
        from importlib.metadata import version as pkg_version
        compiled_version = pkg_version("policyengine-uk-compiled")
    except Exception:
        compiled_version = "unknown"
    return {"policyengine_uk_compiled": compiled_version}
