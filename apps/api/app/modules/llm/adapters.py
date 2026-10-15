"""LLM provider adapters (spec §10).

The connectivity check retrieves the configured model's metadata. This proves
the key, network path, and model ID are valid without generating tokens, so
the check itself is not billed. Only the status line and headers are read,
never the body. Attempts share the timeout: each one gets what is left of it
(for connecting, sending, and each read), and a retry happens only if its
wait fits. It retries at most twice, and only when the provider provably did
not accept the request (LLM-03): the connection failed before anything was
sent, or a ``429``/``503`` came with retry guidance short enough to honour.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import httpx

from app.modules.llm.domain import LLMCheckResult

logger = logging.getLogger(__name__)

_MAX_RETRIES = 2
_MAX_BACKOFF_SECONDS = 5.0


def _normalize_status(status: int) -> str:
    """Map an HTTP status to the normalized codes of LLM-02."""
    if status in (401, 403):
        return "auth_failed"
    if status == 429:
        return "rate_limited"
    if status >= 500:
        return "unavailable"
    return "invalid_request"  # including 404: the configured model does not exist


def _retry_delay(response: httpx.Response) -> float | None:
    """The provider's own retry guidance, if it is short enough to wait for."""
    if response.status_code not in (429, 503):
        return None
    retry_after = response.headers.get("retry-after", "")
    # Delay-seconds are ASCII digits; ``isdigit`` alone also accepts superscripts that ``float`` rejects.
    if not (retry_after.isascii() and retry_after.isdigit()) or float(retry_after) > _MAX_BACKOFF_SECONDS:
        return None
    return float(retry_after)


class _HttpModelCheck:
    name = "http"

    def __init__(
        self,
        *,
        model: str,
        timeout_seconds: float,
        base_url: str,
        headers: dict[str, str],
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._model = model
        self._timeout = timeout_seconds
        self._sleep = sleep
        self._client = httpx.Client(base_url=base_url, headers=headers, timeout=timeout_seconds)

    def _path(self) -> str:
        raise NotImplementedError

    def check(self) -> LLMCheckResult:
        started = time.monotonic()
        deadline = started + self._timeout
        error = "unavailable"
        for attempt in range(_MAX_RETRIES + 1):
            delay: float | None = None
            try:
                remaining = max(deadline - time.monotonic(), 0.001)
                with self._client.stream("GET", self._path(), timeout=remaining) as response:
                    status, guidance = response.status_code, _retry_delay(response)
            except httpx.ConnectTimeout:
                # Nothing was sent, so the provider never saw the request.
                error, delay = "timeout", min(0.5 * 2**attempt, _MAX_BACKOFF_SECONDS)
            except httpx.ConnectError:
                error, delay = "unavailable", min(0.5 * 2**attempt, _MAX_BACKOFF_SECONDS)
            except httpx.TimeoutException:
                error = "timeout"  # the request may have been accepted: never retried
            except httpx.HTTPError:
                error = "unavailable"
            else:
                if status == 200:
                    return self._result(True, started, None)
                error, delay = _normalize_status(status), guidance
            if delay is None or attempt == _MAX_RETRIES or time.monotonic() + delay >= deadline:
                break
            self._sleep(delay)
        logger.warning("llm_check_failed", extra={"provider": self.name, "error_code": error})
        return self._result(False, started, error)

    def _result(self, ok: bool, started: float, error: str | None) -> LLMCheckResult:
        latency = int((time.monotonic() - started) * 1000)
        return LLMCheckResult(ok=ok, provider=self.name, model=self._model, latency_ms=latency, error_code=error)


class AnthropicProvider(_HttpModelCheck):
    name = "anthropic"

    def __init__(
        self, *, api_key: str, model: str, timeout_seconds: float, sleep: Callable[[float], None] = time.sleep
    ) -> None:
        super().__init__(
            model=model,
            timeout_seconds=timeout_seconds,
            base_url="https://api.anthropic.com",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            sleep=sleep,
        )

    def _path(self) -> str:
        return f"/v1/models/{self._model}"


class OpenAIProvider(_HttpModelCheck):
    name = "openai"

    def __init__(
        self, *, api_key: str, model: str, timeout_seconds: float, sleep: Callable[[float], None] = time.sleep
    ) -> None:
        super().__init__(
            model=model,
            timeout_seconds=timeout_seconds,
            base_url="https://api.openai.com",
            headers={"Authorization": f"Bearer {api_key}"},
            sleep=sleep,
        )

    def _path(self) -> str:
        return f"/v1/models/{self._model}"


class MockLLMProvider:
    """Deterministic provider for tests and mock mode (LLM-08)."""

    name = "mock"

    def __init__(self, *, ok: bool = True, error_code: str | None = None) -> None:
        self._ok = ok
        self._error = error_code

    def check(self) -> LLMCheckResult:
        return LLMCheckResult(ok=self._ok, provider=self.name, model="mock-model", latency_ms=0, error_code=self._error)
