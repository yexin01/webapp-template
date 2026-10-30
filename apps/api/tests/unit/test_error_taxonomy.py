"""A module defines its own errors: each carries its HTTP status and code, and the kernel maps it.

The status is declared on the error class (4xx, or 503), so a module never edits the kernel to add
an outcome, and a class that declares none cannot be defined (it would have been a silent 500).
"""

from __future__ import annotations

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.kernel import errors
from app.kernel.errors import ConflictError, DomainError
from app.kernel.http.app import build_fastapi
from tests.conftest import make_settings


class ArchivedError(ConflictError):
    """A module error that reuses a kernel category and adds its own code."""

    code = "ARCHIVED"
    default_message = "This item is archived."


class GoneError(DomainError):
    """A module error with a status the kernel has no class for."""

    code = "GONE"
    status = 410
    default_message = "This item is gone."


class QuotaProviderDownError(DomainError):
    code = "QUOTA_PROVIDER_DOWN"
    status = 503


def client() -> TestClient:
    router = APIRouter()

    @router.get("/archived")
    def archived() -> None:
        raise ArchivedError()

    @router.get("/gone")
    def gone() -> None:
        raise GoneError()

    @router.get("/down")
    def down() -> None:
        raise QuotaProviderDownError()

    @router.get("/bare")
    def bare() -> None:
        raise DomainError()

    app = build_fastapi(settings=make_settings(), title="t", routers=[router], worker_threads=4)
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    ("path", "status", "code"),
    [
        ("/archived", 409, "ARCHIVED"),
        ("/gone", 410, "GONE"),
        ("/down", 503, "QUOTA_PROVIDER_DOWN"),
        ("/bare", 500, "INTERNAL_ERROR"),
    ],
)
def test_the_kernel_maps_a_module_error_by_its_declared_status(path: str, status: int, code: str) -> None:
    response = client().get(f"/api/v1{path}")
    assert response.status_code == status
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == code
    assert response.json()["status"] == status


def test_a_kernel_error_keeps_its_status() -> None:
    expected = {
        errors.AuthenticationError: 401,
        errors.ForbiddenError: 403,
        errors.EntitlementRequiredError: 403,
        errors.NotFoundError: 404,
        errors.ConflictError: 409,
        errors.InvalidRequestError: 400,
        errors.WebhookSignatureError: 400,
        errors.RateLimitedError: 429,
        errors.BudgetExceededError: 429,
        errors.DependencyUnavailableError: 503,
        errors.NotConfiguredError: 503,
    }
    assert {cls: cls.status for cls in expected} == expected


@pytest.mark.parametrize("status", [200, 302, 500, 502, 504, 599])
def test_an_error_must_not_declare_a_status_that_is_neither_a_client_error_nor_503(status: int) -> None:
    with pytest.raises(TypeError, match="declare status"):
        type("Bad", (DomainError,), {"code": "BAD", "status": status})


def test_an_error_that_declares_no_status_cannot_be_defined() -> None:
    """Inherited from DomainError itself, 500 would be a response that commits the request's writes."""
    with pytest.raises(TypeError, match="declare status"):
        type("Forgot", (DomainError,), {"code": "FORGOT"})


def test_a_subclass_of_a_category_inherits_its_status() -> None:
    assert ArchivedError.status == 409
    assert ArchivedError().public_message == "This item is archived."
