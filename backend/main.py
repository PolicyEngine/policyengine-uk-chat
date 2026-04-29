"""
FastAPI entrypoint for the microsim public chatbot.
"""

import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import routes.billing as billing
import routes.chatbot as chatbot
import routes.conversations as conversations

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].lstrip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key or key in os.environ:
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            os.environ[key] = value
    except OSError:
        logger.warning("Could not read env file at %s", path)


_repo_root = Path(__file__).resolve().parent.parent
_load_env_file(_repo_root / ".env")

_hostnames_env = os.environ.get("HOSTNAMES", "")
HOSTNAMES = _hostnames_env.split(",") if _hostnames_env else ["*"]


class NaNSafeJSONResponse(JSONResponse):
    """JSON response that converts NaN/Inf to null."""
    def render(self, content) -> bytes:
        import json, math
        def convert(obj):
            if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
                return None
            if isinstance(obj, dict):
                return {k: convert(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [convert(v) for v in obj]
            return obj
        return json.dumps(convert(content)).encode("utf-8")


app = FastAPI(
    title="Microsim Public Chatbot API",
    version="1.0.0",
    default_response_class=NaNSafeJSONResponse,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=HOSTNAMES,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(billing.router)
app.include_router(chatbot.router)
app.include_router(conversations.router)


@app.on_event("startup")
def startup():
    conversations.ensure_table()


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
