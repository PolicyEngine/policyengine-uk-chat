"""HTTP client for the token-protected deployed evaluation endpoint."""

from collections.abc import Sequence
from typing import Any

import httpx
from pydantic import ValidationError

from eval.schemas import EvalChatResponse


class DeployedEvalError(RuntimeError):
    """A deployed trial failed before producing a valid eval response."""


class DeployedEvalClient:
    def __init__(
        self,
        *,
        backend_url: str,
        token: str,
        timeout_seconds: float = 600,
        http_client: httpx.AsyncClient | None = None,
    ):
        self.backend_url = backend_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds
        self._http_client = http_client
        self._owns_client = http_client is None

    def _client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout_seconds)
            )
        return self._http_client

    async def run_turn(
        self,
        *,
        messages: Sequence[dict[str, Any]],
        session_id: str,
        charts_mode: bool = False,
    ) -> EvalChatResponse:
        # Keep the remote client independent from the deployed application's
        # route package. The server owns request validation; importing
        # ``chat.schemas`` here also executes ``chat.__init__`` and pulls the
        # entire FastAPI stack into the lightweight Modal eval worker.
        request_payload = {
            "messages": list(messages),
            "session_id": session_id,
            "charts_mode": charts_mode,
        }
        try:
            response = await self._client().post(
                f"{self.backend_url}/eval/chat/message",
                headers={"X-Eval-Token": self.token},
                json=request_payload,
                timeout=self.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise DeployedEvalError(
                f"deployed eval request timed out after {self.timeout_seconds:g}s"
            ) from exc
        except httpx.HTTPError as exc:
            raise DeployedEvalError(
                f"deployed eval request failed: {type(exc).__name__}"
            ) from exc

        if not response.is_success:
            raise DeployedEvalError(
                f"deployed eval endpoint returned HTTP {response.status_code}"
            )
        try:
            return EvalChatResponse.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise DeployedEvalError(
                "deployed eval endpoint returned an invalid response"
            ) from exc

    async def aclose(self) -> None:
        if self._owns_client and self._http_client is not None:
            await self._http_client.aclose()
