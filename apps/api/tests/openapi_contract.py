"""The committed OpenAPI document (packages/contracts/openapi.json) as a checker of what the API sends.

DEC-007 makes that document the frontend's source of truth, so a response is right only if the document
says it can happen: its status is declared for the operation, its content type is the declared one, and its
body validates against the declared schema. Standard library only: the validator below understands exactly
the keywords the document uses and raises for any other, so a new keyword cannot be silently ignored.

Test code only. ``Recorder`` is an ASGI middleware that the test harness puts around every app it builds;
``tests/conftest.py`` checks everything it saw when the session ends.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

CONTRACT_PATH = Path(__file__).resolve().parents[3] / "packages" / "contracts" / "openapi.json"
MAX_BODY_BYTES = 1_000_000

# Statuses the platform answers outside any operation's own logic: the body-size limit and the
# last-resort handler. They are not declared per operation, but their bodies are still a Problem.
PLATFORM_STATUSES = {413, 500}
PROBLEM = "#/components/schemas/Problem"
PROBLEM_MEDIA_TYPE = "application/problem+json"

VALIDATING = {"$ref", "type", "properties", "required", "additionalProperties", "items", "enum", "const", "anyOf"}
STRING_RULES = {"format", "minLength", "maxLength"}
ANNOTATIONS = {"title", "description", "default", "examples", "example", "deprecated", "readOnly", "writeOnly"}


class UnsupportedSchemaError(AssertionError):
    """The document uses a keyword the checker does not understand: teach it before trusting a pass."""


def _type_matches(value: Any, name: str) -> bool:
    match name:
        case "string":
            return isinstance(value, str)
        case "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        case "number":
            return isinstance(value, int | float) and not isinstance(value, bool)
        case "boolean":
            return isinstance(value, bool)
        case "null":
            return value is None
        case "array":
            return isinstance(value, list)
        case "object":
            return isinstance(value, dict)
    raise UnsupportedSchemaError(f"type {name!r}")


def _same(left: Any, right: Any) -> bool:
    return type(left) is type(right) and left == right


def _describe(value: Any) -> str:
    return "null" if value is None else type(value).__name__


def _format_problem(name: str, value: str) -> str | None:
    try:
        if name == "uuid":
            uuid.UUID(value)
        elif name == "date-time":
            if datetime.fromisoformat(value).tzinfo is None:
                return "a date-time needs a UTC offset"
        else:
            raise UnsupportedSchemaError(f"format {name!r}")
    except ValueError:
        return f"not a valid {name}"
    return None


def validate(schema: dict[str, Any], value: Any, document: dict[str, Any], path: str = "$") -> list[str]:
    """What is wrong with ``value`` under ``schema``, one line each; empty when it conforms.

    An object with declared properties is closed unless the schema says otherwise: a field the document does
    not name is as much a contract breach as a missing one, because clients are generated from the document.
    """
    errors: list[str] = []
    if "$ref" in schema:
        target: Any = document
        for part in schema["$ref"].removeprefix("#/").split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        errors += validate(target, value, document, path)
    unknown = set(schema) - VALIDATING - STRING_RULES - ANNOTATIONS
    if unknown:
        raise UnsupportedSchemaError(
            f"{path}: keyword(s) {sorted(unknown)} are not understood by tests/openapi_contract.py"
        )

    if "anyOf" in schema:
        failures = [validate(branch, value, document, path) for branch in schema["anyOf"]]
        if all(failures):
            errors.append(
                f"{path}: matches none of {len(failures)} alternatives ({'; '.join(sorted({e for f in failures for e in f}))[:300]})"
            )
    if "const" in schema and not _same(value, schema["const"]):
        errors.append(f"{path}: must be {schema['const']!r}")
    if "enum" in schema and not any(_same(value, option) for option in schema["enum"]):
        errors.append(f"{path}: {value!r} is not one of {schema['enum']}")
    if "type" in schema:
        names = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_type_matches(value, name) for name in names):
            return [*errors, f"{path}: expected {' or '.join(names)}, got {_describe(value)}"]

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        errors += [
            f"{path}: missing required property {name!r}" for name in schema.get("required", []) if name not in value
        ]
        for name, child in properties.items():
            if name in value:
                errors += validate(child, value[name], document, f"{path}.{name}")
        extra = schema.get("additionalProperties", not properties)
        for name in value.keys() - properties.keys():
            if extra is False:
                errors.append(f"{path}: the document does not declare property {name!r}")
            elif isinstance(extra, dict):
                errors += validate(extra, value[name], document, f"{path}.{name}")
    elif isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            errors += validate(schema["items"], item, document, f"{path}[{index}]")
    elif isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{path}: shorter than {schema['minLength']}")
        if len(value) > schema.get("maxLength", len(value)):
            errors.append(f"{path}: longer than {schema['maxLength']}")
        if "format" in schema and (problem := _format_problem(schema["format"], value)):
            errors.append(f"{path}: {problem}")
    return errors


@dataclass(frozen=True)
class Record:
    method: str
    path: str
    status: int
    content_type: str
    body: bytes


@dataclass(frozen=True)
class Operation:
    method: str
    template: str
    spec: dict[str, Any]
    pattern: re.Pattern[str]

    @property
    def literal_length(self) -> int:
        return len(re.sub(r"\{[^}]+\}", "", self.template))


class Contract:
    def __init__(self, document: dict[str, Any] | None = None) -> None:
        self.document: dict[str, Any] = document if document is not None else json.loads(CONTRACT_PATH.read_text())
        self.operations: list[Operation] = [
            Operation(
                method.upper(),
                template,
                spec,
                re.compile("^" + re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(template)) + "$"),
            )
            for template, item in self.document["paths"].items()
            for method, spec in item.items()
        ]

    def operation(self, method: str, path: str) -> Operation | None:
        """The operation a request belongs to, the most literal template when several fit."""
        found = [op for op in self.operations if op.method == method.upper() and op.pattern.match(path)]
        return max(found, key=lambda op: op.literal_length, default=None)

    def conformance(self, record: Record) -> list[str]:
        """Why a response does not match the document; empty when it does or belongs to no operation."""
        operation = self.operation(record.method, record.path)
        if operation is None:
            return []
        label = f"{operation.method} {operation.template} -> {record.status}"
        responses = operation.spec["responses"]
        declared = (
            responses.get(str(record.status)) or responses.get(f"{record.status // 100}XX") or responses.get("default")
        )
        if declared is None:
            if record.status not in PLATFORM_STATUSES:
                return [f"{label}: the document declares only {', '.join(sorted(responses))}"]
            declared = {"content": {PROBLEM_MEDIA_TYPE: {"schema": {"$ref": PROBLEM}}}}
        content = declared.get("content")
        if not content:
            return [f"{label}: the document declares no body, but one was sent"] if record.body else []
        media = record.content_type.split(";")[0].strip().lower()
        if media not in content:
            return [
                f"{label}: sent as {media or 'no content type'}, the document declares {', '.join(sorted(content))}"
            ]
        try:
            body = json.loads(record.body)
        except ValueError:
            return [f"{label}: the body is not JSON"]
        return [f"{label}: {problem}" for problem in validate(content[media]["schema"], body, self.document)]

    def check(self, records: list[Record]) -> list[str]:
        """Every distinct problem of ``records``, with how many responses had it."""
        counts: dict[str, int] = {}
        for record in records:
            for problem in self.conformance(record):
                counts[problem] = counts.get(problem, 0) + 1
        return [f"{problem} (x{count})" if count > 1 else problem for problem, count in sorted(counts.items())]


class Recorder:
    """ASGI middleware that records every HTTP response into ``sink`` and changes nothing."""

    def __init__(self, app: Any, sink: list[Record]) -> None:
        self.app = app
        self.sink = sink

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started: dict[str, Any] = {}
        chunks: list[bytes] = []

        async def capture(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                started["status"] = message["status"]
                started["headers"] = {k.decode().lower(): v.decode() for k, v in message["headers"]}
            elif message["type"] == "http.response.body" and sum(map(len, chunks)) < MAX_BODY_BYTES:
                chunks.append(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, receive, capture)
        finally:
            if started:
                self.sink.append(
                    Record(
                        scope["method"],
                        scope["path"],
                        started["status"],
                        started["headers"].get("content-type", ""),
                        b"".join(chunks),
                    )
                )


RECORDS: list[Record] = []
