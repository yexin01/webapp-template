"""The production telemetry setup, driven through the real application, for ``test_telemetry_wire.py``.

``sentry_sdk.init`` patches libraries for the life of the process, so this runs in a process of its own:
``bootstrap`` (logging, Sentry, PostHog, the composition root), then requests that carry canary secrets, emails,
and prompt text, and errors that quote them. Sentry and PostHog point at a loopback stub the parent reads; the
provider the app calls is another stub, because that call legitimately carries its own query string and the IDs of
its own objects in its paths. A signed-in user's request goes through the production Clerk verifier and directory,
so the first request reads their memberships from that provider, as it does in production.
Usage: ``python -m tests.telemetry_child TELEMETRY_URL PROVIDER_URL DATABASE_URL RESULT_FILE``.
"""

from __future__ import annotations

import base64
import json
import logging
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import jwt
import sentry_sdk
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
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
# The IDs of the identity provider's user and organization, and of a payment provider's customer: they travel in the
# paths of requests to this API and of its calls to those providers, never in telemetry.
CLERK_USER = "user_" + "CANARYCLERKUSER1"
CLERK_ORG = "org_" + "CANARYCLERKORG1"
STRIPE_CUSTOMER = "cus_" + "CANARYCUSTOMER1"
CANARIES = [SECRET, WEBHOOK_SECRET, EMAIL, TOKEN, TICKET, PROMPT, CLERK_USER, CLERK_ORG, STRIPE_CUSTOMER, "CANARY"]
ORIGIN = "http://localhost:3000"
FRONTEND_HOST = "canary-app.clerk.accounts.dev"

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
        with httpx.Client() as client:  # spans and breadcrumbs with a query string, and with the provider's ID
            client.get(f"{provider_url}/v1/customers?email={EMAIL}&api_key={SECRET}#{TICKET}")
            client.get(f"{provider_url}/v1/customers/{STRIPE_CUSTOMER}")
        canary.error("lookup failed for %s", EMAIL)
        raise RuntimeError("lookup failed")

    @router.post("/body")
    async def body(request: Request) -> None:
        payload = await request.json()
        canary.error("rejected a body with %d fields", len(payload))
        raise RuntimeError("rejected")

    @router.get("/orgs/{org_id}/boom")
    def org_boom(org_id: str) -> None:
        raise RuntimeError("an organization route failed")

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


def clerk_session(private: rsa.RSAPrivateKey) -> str:
    """A session token of ``CLERK_USER`` that the production verifier accepts."""
    now = int(time.time())
    claims = {"sub": CLERK_USER, "iss": f"https://{FRONTEND_HOST}", "azp": ORIGIN, "iat": now, "exp": now + 300, "v": 2}
    return jwt.encode(claims, private, algorithm="RS256")


def main(telemetry_url: str, provider_url: str, database_url: str, result: Path) -> None:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    settings = make_settings(
        database_url=database_url,
        analytics_mode="sandbox",
        posthog_project_api_key="phc_test_only",
        posthog_host=telemetry_url,
        error_reporting_mode="sandbox",
        sentry_dsn=f"http://public@{telemetry_url.removeprefix('http://')}/1",
        sentry_traces_sample_rate=1.0,
        clerk_publishable_key="pk_test_" + base64.b64encode(f"{FRONTEND_HOST}$".encode()).decode(),
        clerk_secret_key="sk_test_only_abcdefghijklmnop",
        clerk_jwt_key=public_pem.decode(),
        clerk_authorized_parties=ORIGIN,
        clerk_api_url=provider_url,
    )
    container = bootstrap(settings)
    app = create_app(container)
    app.include_router(build_router(container, provider_url))
    client = TestClient(app, raise_server_exceptions=False)
    auth = {"Authorization": f"Bearer {TOKEN}", "X-Api-Key": SECRET, "Stripe-Signature": WEBHOOK_SECRET}
    query = f"?token={SECRET}&email={EMAIL}&__clerk_ticket={TICKET}&q={PROMPT.replace(' ', '+')}"

    statuses: dict[str, int] = {}
    arrived: dict[str, bool] = {}  # at the stub by the time the client had its answer
    session = {"Authorization": f"Bearer {clerk_session(private)}"}
    statuses["me"] = client.get(f"/api/v1/me{query}", headers=auth).status_code
    statuses["unmatched"] = client.get(f"/api/v1/orgs/{EMAIL}/nothing{query}", headers=auth).status_code
    statuses["organization"] = client.get(f"/api/v1/orgs/{CLERK_ORG}/projects{query}", headers=session).status_code
    statuses["unmatched_organization"] = client.get(f"/api/v1/orgs/{CLERK_ORG}/nothing", headers=session).status_code
    statuses["organization_boom"] = client.get(f"/api/v1/_canary/orgs/{CLERK_ORG}/boom", headers=session).status_code
    arrived["organization_boom"] = seen(telemetry_url, "an organization route failed")
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
    main(sys.argv[1], sys.argv[2], sys.argv[3], Path(sys.argv[4]))
