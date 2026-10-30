"""The response checker itself (tests/openapi_contract.py): what it accepts, what it flags, and that it refuses
to pass on a schema keyword it does not understand. The checks it makes on the API are in
tests/integration/test_contract_conformance.py and at the end of every test session (tests/conftest.py)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest

from tests.openapi_contract import Contract, Record, Recorder, UnsupportedSchemaError, validate

DOCUMENT: dict[str, Any] = {
    "paths": {
        "/api/v1/projects": {
            "get": {
                "responses": {
                    "200": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Page"}}}},
                    "400": {
                        "content": {"application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}}
                    },
                }
            },
            "post": {"responses": {"201": {"content": {"application/json": {"schema": {"type": "object"}}}}}},
        },
        "/api/v1/projects/{project_id}": {
            "delete": {"responses": {"204": {"description": "gone"}}},
            "get": {"responses": {"200": {"content": {"application/json": {"schema": {"type": "object"}}}}}},
        },
        "/api/v1/projects/latest": {
            "get": {"responses": {"200": {"content": {"application/json": {"schema": {"type": "object"}}}}}}
        },
        "/api/v1/things/{kind}/{id}": {
            "get": {"responses": {"default": {"content": {"application/json": {"schema": {"type": "object"}}}}}}
        },
    },
    "components": {
        "schemas": {
            "Problem": {
                "type": "object",
                "required": ["status", "code"],
                "properties": {"status": {"type": "integer"}, "code": {"type": "string"}},
            },
            "Page": {
                "type": "object",
                "required": ["items", "next"],
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["id"],
                            "properties": {"id": {"type": "string", "format": "uuid"}},
                        },
                    },
                    "next": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                },
            },
        }
    },
}
UUID = "00000000-0000-4000-8000-000000000000"


def problems(schema: dict[str, Any], value: Any) -> list[str]:
    return validate(schema, value, DOCUMENT)


def test_types_required_properties_and_nullable_alternatives() -> None:
    page = {"$ref": "#/components/schemas/Page"}
    assert problems(page, {"items": [{"id": UUID}], "next": None}) == []
    assert problems(page, {"items": [], "next": "abc"}) == []
    assert problems(page, {"items": []}) == ["$: missing required property 'next'"]
    assert problems(page, {"items": {}, "next": None}) == ["$.items: expected array, got dict"]
    assert problems(page, {"items": [], "next": 3}) == [
        "$.next: matches none of 2 alternatives ($.next: expected null, got int; $.next: expected string, got int)"
    ]
    assert problems(page, [])[0] == "$: expected object, got list"


def test_an_object_is_closed_unless_the_document_opens_it() -> None:
    page = {"$ref": "#/components/schemas/Page"}
    assert problems(page, {"items": [], "next": None, "total": 3}) == [
        "$: the document does not declare property 'total'"
    ]
    assert problems({"type": "object"}, {"anything": 1}) == []
    assert problems({"type": "object", "additionalProperties": {"enum": ["ok"]}}, {"a": "ok", "b": "no"}) == [
        "$.b: 'no' is not one of ['ok']"
    ]
    assert problems({"type": "object", "additionalProperties": False}, {"a": 1}) == [
        "$: the document does not declare property 'a'"
    ]


def test_scalars_are_checked_strictly() -> None:
    assert problems({"type": "integer"}, True) == ["$: expected integer, got bool"]
    assert problems({"type": "integer"}, 1.5) == ["$: expected integer, got float"]
    assert problems({"type": "boolean"}, 1) == ["$: expected boolean, got int"]
    assert problems({"const": True}, 1) == ["$: must be True"]
    assert problems({"enum": [1]}, True) == ["$: True is not one of [1]"]
    assert problems({"type": "string", "minLength": 2, "maxLength": 3}, "a") == ["$: shorter than 2"]
    assert problems({"type": "string", "maxLength": 3}, "abcd") == ["$: longer than 3"]
    assert problems({"type": "string", "format": "uuid"}, "nope") == ["$: not a valid uuid"]
    assert problems({"type": "string", "format": "date-time"}, "2026-06-01T12:00:00Z") == []
    assert problems({"type": "string", "format": "date-time"}, "2026-06-01T12:00:00") == [
        "$: a date-time needs a UTC offset"
    ]
    assert problems({"type": "string", "format": "date-time"}, "yesterday") == ["$: not a valid date-time"]


def test_a_keyword_the_checker_does_not_know_is_an_error_not_a_pass() -> None:
    for schema in (
        {"oneOf": []},
        {"type": "string", "pattern": "^a"},
        {"type": "integer", "minimum": 1},
        {"allOf": []},
    ):
        with pytest.raises(UnsupportedSchemaError, match="not understood"):
            problems(schema, "a")
    with pytest.raises(UnsupportedSchemaError, match="format 'email'"):
        problems({"type": "string", "format": "email"}, "a@b.c")
    with pytest.raises(UnsupportedSchemaError, match="type 'float'"):
        problems({"type": "float"}, 1)
    assert problems({"type": "string", "title": "T", "description": "d", "default": "x"}, "a") == []


contract = Contract(DOCUMENT)


def record(method: str, path: str, status: int, body: Any = None, media: str = "application/json") -> Record:
    return Record(method, path, status, media, b"" if body is None else json.dumps(body).encode())


def test_a_path_belongs_to_the_most_literal_template_and_to_its_own_method() -> None:
    assert contract.operation("GET", "/api/v1/projects/latest").template == "/api/v1/projects/latest"  # type: ignore[union-attr]
    assert contract.operation("DELETE", "/api/v1/projects/latest").template == "/api/v1/projects/{project_id}"  # type: ignore[union-attr]
    assert contract.operation("GET", f"/api/v1/projects/{UUID}").template == "/api/v1/projects/{project_id}"  # type: ignore[union-attr]
    assert contract.operation("get", "/api/v1/things/a/b").template == "/api/v1/things/{kind}/{id}"  # type: ignore[union-attr]
    for method, path in (
        ("PUT", "/api/v1/projects/x"),
        ("PUT", "/api/v1/projects"),
        ("GET", "/api/v1/nothing"),
        ("GET", "/api/v1/things/a"),
    ):
        assert contract.operation(method, path) is None


def test_a_response_that_the_document_describes_conforms() -> None:
    assert contract.conformance(record("GET", "/api/v1/projects", 200, {"items": [], "next": None})) == []
    assert (
        contract.conformance(
            record(
                "GET", "/api/v1/projects", 400, {"status": 400, "code": "INVALID_REQUEST"}, "application/problem+json"
            )
        )
        == []
    )
    assert contract.conformance(record("DELETE", f"/api/v1/projects/{UUID}", 204)) == []
    assert contract.conformance(record("GET", "/api/v1/things/a/b", 418, {})) == []  # 'default'
    assert contract.conformance(record("GET", "/api/v1/unknown", 500)) == []  # not an operation of the document


def test_a_status_the_document_does_not_declare_is_reported_with_the_declared_ones() -> None:
    assert contract.conformance(
        record("GET", "/api/v1/projects", 503, {"status": 503, "code": "X"}, "application/problem+json")
    ) == ["GET /api/v1/projects -> 503: the document declares only 200, 400"]


def test_the_wrong_content_type_or_body_is_reported() -> None:
    wrong_type = contract.conformance(record("GET", "/api/v1/projects", 400, {"status": 400, "code": "X"}))
    assert wrong_type == [
        "GET /api/v1/projects -> 400: sent as application/json, the document declares application/problem+json"
    ]
    body = contract.conformance(
        record("GET", "/api/v1/projects", 400, {"status": "400", "code": "X"}, "application/problem+json")
    )
    assert body == ["GET /api/v1/projects -> 400: $.status: expected integer, got str"]
    assert contract.conformance(Record("GET", "/api/v1/projects", 200, "application/json", b"<html>")) == [
        "GET /api/v1/projects -> 200: the body is not JSON"
    ]
    assert contract.conformance(record("DELETE", f"/api/v1/projects/{UUID}", 204, {})) == [
        "DELETE /api/v1/projects/{project_id} -> 204: the document declares no body, but one was sent"
    ]
    assert contract.conformance(Record("GET", "/api/v1/projects", 200, "", b"{}"))[0].endswith(
        "sent as no content type, the document declares application/json"
    )


def test_the_platform_answers_413_and_500_as_problems_without_declaring_them_everywhere() -> None:
    for status in (413, 500):
        problem = record(
            "POST", "/api/v1/projects", status, {"status": status, "code": "X"}, "application/problem+json"
        )
        assert contract.conformance(problem) == []
        broken = record("POST", "/api/v1/projects", status, {"oops": 1}, "application/problem+json")
        assert contract.conformance(broken) == [
            f"POST /api/v1/projects -> {status}: $: missing required property 'status'",
            f"POST /api/v1/projects -> {status}: $: missing required property 'code'",
            f"POST /api/v1/projects -> {status}: $: the document does not declare property 'oops'",
        ]
    assert (
        "sent as application/json"
        in contract.conformance(record("POST", "/api/v1/projects", 500, {"status": 500, "code": "X"}))[0]
    )


def test_distinct_problems_are_counted() -> None:
    bad = record("GET", "/api/v1/projects", 503, {"status": 503, "code": "X"}, "application/problem+json")
    other = record("GET", "/api/v1/projects", 401, {"status": 401, "code": "X"}, "application/problem+json")
    fine = record("GET", "/api/v1/projects", 200, {"items": [], "next": None})
    assert contract.check([bad, bad, bad, other, fine]) == [
        "GET /api/v1/projects -> 401: the document declares only 200, 400",
        "GET /api/v1/projects -> 503: the document declares only 200, 400 (x3)",
    ]


def run_asgi(app: Any, scope: dict[str, Any]) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    anyio.run(app, scope, receive, send)
    return sent


def test_the_recorder_keeps_method_path_status_type_and_body_and_changes_nothing() -> None:
    async def inner(scope: dict[str, Any], receive: Any, send: Any) -> None:
        await send(
            {"type": "http.response.start", "status": 400, "headers": [(b"Content-Type", b"application/problem+json")]}
        )
        await send({"type": "http.response.body", "body": b'{"status"', "more_body": True})
        await send({"type": "http.response.body", "body": b": 400}", "more_body": False})

    sink: list[Record] = []
    sent = run_asgi(Recorder(inner, sink), {"type": "http", "method": "GET", "path": "/api/v1/projects"})
    assert [m["type"] for m in sent] == ["http.response.start", "http.response.body", "http.response.body"]
    assert sink == [Record("GET", "/api/v1/projects", 400, "application/problem+json", b'{"status": 400}')]


def test_the_recorder_ignores_other_scopes_and_a_response_that_never_started() -> None:
    called: list[str] = []

    async def inner(scope: dict[str, Any], receive: Any, send: Any) -> None:
        called.append(scope["type"])
        if scope["type"] == "http":
            raise RuntimeError("boom")

    sink: list[Record] = []
    run_asgi(Recorder(inner, sink), {"type": "lifespan"})
    with pytest.raises(RuntimeError):
        run_asgi(Recorder(inner, sink), {"type": "http", "method": "GET", "path": "/x"})
    assert called == ["lifespan", "http"]
    assert sink == []


def test_the_committed_document_declares_its_errors_as_problem_details_on_every_operation() -> None:
    """API-02 and DEC-007: a client generated from the document types an error as the Problem the API sends."""
    real = Contract()
    problem_refs = {"#/components/schemas/Problem", "#/components/schemas/ValidationProblem"}
    wrong: list[str] = []
    for operation in real.operations:
        for status, response in operation.spec["responses"].items():
            if int(status) < 400:
                continue
            for media, body in response.get("content", {}).items():
                if body["schema"].get("$ref") in problem_refs and media != "application/problem+json":
                    wrong.append(f"{operation.method} {operation.template} {status}: {media}")
    assert wrong == []


def test_the_committed_document_declares_what_every_authenticated_operation_can_answer() -> None:
    """Every operation behind a session can answer 401 (no or a bad token) and 503 (the database or the identity
    provider is unavailable); an organization route also 404 (not a member: TEN-05)."""
    real = Contract()
    open_routes = {"/api/v1/health", "/api/v1/ready", "/api/v1/webhooks/stripe", "/api/v1/webhooks/clerk"}
    missing: list[str] = []
    for operation in real.operations:
        if operation.template in open_routes:
            continue
        needed = {"401", "503"} | ({"404"} if "{org_id}" in operation.template else set())
        absent = sorted(needed - set(operation.spec["responses"]))
        if absent:
            missing.append(f"{operation.method} {operation.template}: {', '.join(absent)}")
    assert missing == []


API_DIR = Path(__file__).resolve().parents[2]
SESSION_CONFTEST = (
    "from tests.conftest import _contract_exempt, pytest_sessionfinish, pytest_terminal_summary  # noqa: F401\n"
)


def run_session(tmp_path: Path, record: str) -> subprocess.CompletedProcess[str]:
    """A test session of its own, with the harness's end-of-session check, in which one test sends ``record``."""
    (tmp_path / "conftest.py").write_text(SESSION_CONFTEST)
    (tmp_path / "test_probe.py").write_text(
        f"from tests.openapi_contract import RECORDS, Record\n\n\ndef test_response():\n    RECORDS.append({record})\n"
    )
    return subprocess.run(  # noqa: S603 - fixed arguments, our own interpreter
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-c",
            str(API_DIR / "pyproject.toml"),
            str(tmp_path),
        ],
        cwd=API_DIR,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def test_a_session_whose_apps_sent_a_response_the_document_does_not_describe_fails(tmp_path: Path) -> None:
    result = run_session(tmp_path, 'Record("GET", "/api/v1/me", 200, "text/html", b"<html>")')
    output = result.stdout + result.stderr
    assert "1 passed" in output
    assert result.returncode == 1, output
    assert "1 responses differ from packages/contracts/openapi.json" in output
    assert "GET /api/v1/me -> 200: sent as text/html, the document declares application/json" in output


def test_a_session_whose_responses_all_conform_passes(tmp_path: Path) -> None:
    result = run_session(
        tmp_path,
        'Record("GET", "/api/v1/me", 200, "application/json", b\'{"user_id": "00000000-0000-4000-8000-000000000000"}\')',
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "differ from" not in result.stdout


RECORDED_PROBE = """
import anyio
import pytest

from tests.openapi_contract import RECORDS, Recorder


async def inner(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/html")]})
    await send({"type": "http.response.body", "body": b"<html>"})


async def receive():
    return {"type": "http.request", "body": b"", "more_body": False}


async def send(message):
    return None


%s
def test_a_route_sends_what_the_document_does_not_describe():
    anyio.run(Recorder(inner, RECORDS), {"type": "http", "method": "GET", "path": "/api/v1/me"}, receive, send)
"""


def run_recorded_session(tmp_path: Path, marker: str) -> subprocess.CompletedProcess[str]:
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "conftest.py").write_text(SESSION_CONFTEST)
    (tmp_path / "test_probe.py").write_text(RECORDED_PROBE % marker)
    return subprocess.run(  # noqa: S603 - fixed arguments, our own interpreter
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-c",
            str(API_DIR / "pyproject.toml"),
            str(tmp_path),
        ],
        cwd=API_DIR,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def test_a_test_marked_contract_exempt_does_not_count_and_an_unmarked_one_does(tmp_path: Path) -> None:
    exempt = run_recorded_session(tmp_path / "exempt", "@pytest.mark.contract_exempt")
    assert exempt.returncode == 0, exempt.stdout + exempt.stderr
    assert "differ from" not in exempt.stdout
    counted = run_recorded_session(tmp_path / "counted", "")
    assert counted.returncode == 1, counted.stdout + counted.stderr
    assert "sent as text/html, the document declares application/json" in counted.stdout


def test_a_paused_recorder_records_nothing_and_resumes() -> None:
    async def inner(scope: dict[str, Any], receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    sink: list[Record] = []
    scope = {"type": "http", "method": "GET", "path": "/x"}
    Recorder.paused = True
    try:
        run_asgi(Recorder(inner, sink), scope)
        assert sink == []
    finally:
        Recorder.paused = False
    run_asgi(Recorder(inner, sink), scope)
    assert len(sink) == 1
