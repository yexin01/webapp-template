"""The application factory's middleware order."""

from __future__ import annotations

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
