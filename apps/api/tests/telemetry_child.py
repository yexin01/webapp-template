"""The production telemetry setup, driven through the real application, for ``test_telemetry_wire.py``.

``sentry_sdk.init`` patches libraries for the life of the process, so this runs in a process of its own:
``bootstrap`` (logging, Sentry, PostHog, the composition root), then requests that carry canary secrets, emails,
and prompt text, and errors that quote them. Sentry and PostHog point at a loopback stub the parent reads; the
provider the app calls is another stub, because that call legitimately carries its own query string.
Usage: ``python -m tests.telemetry_child TELEMETRY_URL PROVIDER_URL RESULT_FILE``.
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from pathlib import Path
from typing import Any

import httpx
import sentry_sdk
from fastapi import APIRouter, Request
from fastapi.testclient import TestClient

from app.bootstrap import Container, bootstrap, create_app
from app.kernel.errors import DependencyUnavailableError
from tests.conftest import make_settings

# Built at run time: a literal would trip secret scanners, and Sentry sends the source lines around a frame.
SECRET = "sk_" + "live_CANARYSECRET1234567890"
WEBHOOK_SECRET = "whsec_" + "CANARYWEBHOOK1234567890"
EMAIL = "canary.person" + "@example.com"
TOKEN = "eyJhbGciOiJSUzI1NiJ9." + "eyJzdWIiOiJDQU5BUlkifQ." + "c2lnbmF0dXJlQ0FOQVJZ"
TICKET = "CANARYTICKET42"
PROMPT = "Summarize the confidential merger plan of Initech"
CANARIES = [SECRET, WEBHOOK_SECRET, EMAIL, TOKEN, TICKET, PROMPT, "CANARY"]

canary = logging.getLogger("canary")
router = APIRouter(prefix="/api/v1/_canary")
state: dict[str, Any] = {}


def build_router(container: Container, provider_url: str) -> APIRouter:
    @router.get("/boom")
    def boom() -> None:
        raise RuntimeError(f"charge failed for {EMAIL} with key {SECRET}")

    @router.get("/unavailable")
    def unavailable() -> None:
        raise DependencyUnavailableError from ValueError(f"{SECRET} rejected for {EMAIL}")

    @router.get("/misuse")
    def misuse() -> None:
        """What a careless feature does: personal data in every place the SDK takes context."""
        sentry_sdk.set_user({"id": "user_canary", "email": EMAIL, "ip_address": "203.0.113.7"})
        sentry_sdk.set_tag("prompt", PROMPT)
        sentry_sdk.set_tag("owner", EMAIL)
        sentry_sdk.set_extra("messages", [{"role": "user", "content": PROMPT}])
        sentry_sdk.set_context("customer", {"email": EMAIL, "token": TOKEN})
        sentry_sdk.add_breadcrumb(category="app", message=f"looked up {EMAIL}", data={"prompt": PROMPT, "key": SECRET})
        canary.warning("looked up %s with %s", EMAIL, SECRET, extra={"prompt": PROMPT, "authorization": TOKEN})
        with httpx.Client() as client:  # a span and a breadcrumb with a query string
            client.get(f"{provider_url}/v1/customers?email={EMAIL}&api_key={SECRET}#{TICKET}")
        canary.error("lookup failed for %s", EMAIL)
        raise RuntimeError("lookup failed")

    @router.post("/body")
    async def body(request: Request) -> None:
        payload = await request.json()
        canary.error("rejected a body with %d fields", len(payload))
        raise RuntimeError("rejected")

    @router.get("/capture")
    def capture() -> dict[str, bool]:
        user = uuid.uuid4()
        analytics = container.kernel.analytics
        analytics.capture(user, "checkout_started", {"plan": "pro_monthly", "email": EMAIL, "prompt": PROMPT})
        analytics.capture(user, "customer_emailed", {"email": EMAIL})
        return {"queued": True}

    return router


def seen(telemetry_url: str, marker: str) -> bool:
    return bool(httpx.get(f"{telemetry_url}/__seen", params={"marker": marker}).json()["seen"])


def main(telemetry_url: str, provider_url: str, result: Path) -> None:
    settings = make_settings(
        analytics_mode="sandbox",
        posthog_project_api_key="phc_test_only",
        posthog_host=telemetry_url,
        error_reporting_mode="sandbox",
        sentry_dsn=f"http://public@{telemetry_url.removeprefix('http://')}/1",
        sentry_traces_sample_rate=1.0,
    )
    container = bootstrap(settings)
    app = create_app(container)
    app.include_router(build_router(container, provider_url))
    client = TestClient(app, raise_server_exceptions=False)
    auth = {"Authorization": f"Bearer {TOKEN}", "X-Api-Key": SECRET, "Stripe-Signature": WEBHOOK_SECRET}
    query = f"?token={SECRET}&email={EMAIL}&__clerk_ticket={TICKET}&q={PROMPT.replace(' ', '+')}"

    statuses: dict[str, int] = {}
    arrived: dict[str, bool] = {}  # at the stub by the time the client had its answer
    statuses["me"] = client.get(f"/api/v1/me{query}", headers=auth).status_code
    statuses["unmatched"] = client.get(f"/api/v1/orgs/{EMAIL}/nothing{query}", headers=auth).status_code
    statuses["boom"] = client.get(f"/api/v1/_canary/boom{query}", headers=auth).status_code
    arrived["boom"] = seen(telemetry_url, "charge failed")
    statuses["unavailable"] = client.get(f"/api/v1/_canary/unavailable{query}", headers=auth).status_code
    arrived["unavailable"] = seen(telemetry_url, "error_code")
    statuses["misuse"] = client.get(f"/api/v1/_canary/misuse{query}", headers=auth).status_code
    arrived["misuse"] = seen(telemetry_url, "lookup failed")
    statuses["body"] = client.post(
        f"/api/v1/_canary/body{query}",
        json={"email": EMAIL, "prompt": PROMPT, "messages": [{"role": "user", "content": PROMPT}]},
        headers=auth,
    ).status_code
    statuses["capture"] = client.get(f"/api/v1/_canary/capture{query}", headers=auth).status_code
    arrived["capture"] = seen(telemetry_url, "checkout_started")

    sentry_sdk.flush(timeout=5)
    container.close()
    sentry_sdk.get_client().close(timeout=5)
    result.write_text(json.dumps({"statuses": statuses, "arrived": arrived}))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], Path(sys.argv[3]))
