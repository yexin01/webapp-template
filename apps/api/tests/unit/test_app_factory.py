"""The application factory's middleware order and CORS policy."""

from __future__ import annotations

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from starlette.middleware import Middleware
from starlette.types import ASGIApp, Receive, Scope, Send

from app.kernel.http.app import build_fastapi
from app.kernel.request_context import request_id_var
from tests.conftest import make_settings


def test_inner_middleware_wraps_the_routes_first_entry_outermost_inside_the_request_context() -> None:
    events: list[str] = []

    class Recorder:
        def __init__(self, app: ASGIApp, *, name: str) -> None:
            self.app = app
            self.name = name

        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            events.append(f"enter {self.name}, request ID set: {request_id_var.get() is not None}")
            await self.app(scope, receive, send)
            events.append(f"leave {self.name}")

    router = APIRouter()

    @router.get("/probe")
    async def probe() -> dict[str, str]:
        events.append("route")
        return {}

    app = build_fastapi(
        settings=make_settings(),
        title="t",
        routers=[router],
        inner_middleware=[Middleware(Recorder, name="A"), Middleware(Recorder, name="B")],
        worker_threads=4,
    )
    assert TestClient(app).get("/api/v1/probe").status_code == 200
    assert events == [
        "enter A, request ID set: True",
        "enter B, request ID set: True",
        "route",
        "leave B",
        "leave A",
    ]


# -- CORS (API-04) --------------------------------------------------------------------------------------------
# Browsers reach the API through the web app's rewrites; a direct cross-origin call is allowed from APP_BASE_URL only.

APP_ORIGIN = "https://app.example.com"
PREFLIGHT = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization, content-type"}


def cors_client(app_base_url: str = APP_ORIGIN) -> TestClient:
    router = APIRouter()

    @router.get("/probe")
    async def probe() -> dict[str, str]:
        return {}

    app = build_fastapi(
        settings=make_settings(app_base_url=app_base_url), title="t", routers=[router], worker_threads=4
    )
    return TestClient(app)


@pytest.mark.parametrize("origin", [APP_ORIGIN, "https://other-app.example.org"])
def test_a_preflight_from_the_origin_of_app_base_url_is_answered_and_credentials_are_not_allowed(origin: str) -> None:
    response = cors_client(origin).options("/api/v1/probe", headers={"Origin": origin, **PREFLIGHT})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert "access-control-allow-credentials" not in response.headers  # cookies are never accepted (API-04)


@pytest.mark.parametrize(
    "origin", ["https://evil.example", "http://app.example.com", "https://app.example.com.evil.example"]
)
def test_a_preflight_from_any_other_origin_is_refused(origin: str) -> None:
    response = cors_client().options("/api/v1/probe", headers={"Origin": origin, **PREFLIGHT})
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_the_app_origin_is_the_configured_one_not_a_fixed_one() -> None:
    other = "https://other-app.example.org"
    response = cors_client(other).options("/api/v1/probe", headers={"Origin": APP_ORIGIN, **PREFLIGHT})
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize(
    "asked",
    [
        {"Access-Control-Request-Method": "PATCH"},
        {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "x-forwarded-user"},
    ],
    ids=["a method no route uses", "a header no route reads"],
)
def test_a_preflight_for_a_method_or_header_the_api_does_not_take_is_refused(asked: dict[str, str]) -> None:
    response = cors_client().options("/api/v1/probe", headers={"Origin": APP_ORIGIN, **asked})
    assert response.status_code == 400


def test_a_response_to_another_origin_carries_no_allow_origin_header() -> None:
    """The browser withholds a cross-origin response that lacks the header, whatever the server did with the request."""
    response = cors_client().get("/api/v1/probe", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in response.headers
    allowed = cors_client().get("/api/v1/probe", headers={"Origin": APP_ORIGIN})
    assert allowed.headers["access-control-allow-origin"] == APP_ORIGIN
