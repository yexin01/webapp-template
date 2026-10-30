"""OBS-03: a module registers the analytics events it sends at wiring time, under the allowlist's rules.

The kernel keeps the template's own events (ANALYTICS_ALLOWLIST, listed in docs/data-inventory.md). A module
declares the events it adds in ``Contribution.analytics_events``; the composition root registers them at
startup, and a name another event has, a property named like personal data or a secret, or a malformed
name stops the application from starting.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.bootstrap import build_container
from app.kernel.infrastructure.observability import (
    ANALYTICS_ALLOWLIST,
    AnalyticsEvents,
    NoopAnalytics,
    PostHogAnalytics,
    _filter,
)
from app.kernel.wiring import Contribution
from tests.conftest import make_settings
from tests.stub_server import stub_server

NOTE = {"note_created": frozenset({"source", "length"})}


def test_the_kernel_events_are_the_start() -> None:
    events = AnalyticsEvents()
    assert {name: events.properties(name) for name in ANALYTICS_ALLOWLIST} == dict(ANALYTICS_ALLOWLIST)
    assert events.properties("note_created") is None


def test_a_registered_event_is_filtered_like_the_kernel_ones() -> None:
    events = AnalyticsEvents()
    events.register(NOTE)
    assert _filter("note_created", {"source": "api", "length": 3, "title": "Secret plan"}, events) == {
        "source": "api",
        "length": 3,
    }
    assert _filter("note_deleted", {"source": "api"}, events) is None
    assert _filter("note_created", {"source": "api"}) is None  # the default knows the kernel's events only


def test_registrations_do_not_leak_between_containers() -> None:
    first, second = AnalyticsEvents(), AnalyticsEvents()
    first.register(NOTE)
    assert second.properties("note_created") is None
    assert AnalyticsEvents().properties("note_created") is None


@pytest.mark.parametrize(
    ("events", "why"),
    [
        pytest.param({"plan_changed": frozenset({"plan"})}, "already registered", id="a kernel event's name"),
        pytest.param({"Note-Created": frozenset()}, "lower-case snake case", id="a malformed event name"),
        pytest.param({"nc": frozenset()}, "lower-case snake case", id="a name that is too short"),
        pytest.param({"note_created": frozenset({"Source"})}, "lower-case snake case", id="a malformed property"),
        pytest.param({"note_created": frozenset({"email"})}, "personal data or a secret", id="an email property"),
        pytest.param({"note_created": frozenset({"token"})}, "personal data or a secret", id="a token property"),
        pytest.param({"note_created": frozenset({"prompt"})}, "personal data or a secret", id="a prompt property"),
    ],
)
def test_a_registration_the_allowlist_exists_to_prevent_is_refused(events: dict[str, frozenset[str]], why: str) -> None:
    registry = AnalyticsEvents()
    with pytest.raises(ValueError, match=why):
        registry.register(events)
    assert registry.properties("note_created") is None  # nothing of a refused registration is kept


def test_a_second_registration_of_the_same_name_is_refused() -> None:
    registry = AnalyticsEvents()
    registry.register(NOTE)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(NOTE)


def test_one_refused_event_registers_none_of_its_batch() -> None:
    registry = AnalyticsEvents()
    with pytest.raises(ValueError, match="note_shared"):
        registry.register({"note_created": frozenset({"source"}), "note_shared": frozenset({"email"})})
    assert registry.properties("note_created") is None


def test_the_composition_root_registers_what_modules_contribute(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import bootstrap

    container = build_container(make_settings())
    try:
        assert container.kernel.analytics_events.properties("project_created") == frozenset({"context"})
    finally:
        container.close()

    contributed = Contribution(analytics_events=NOTE)
    real = bootstrap.Container.contributions
    monkeypatch.setattr(bootstrap.Container, "contributions", lambda self: [*real(self), contributed])
    container = build_container(make_settings())
    try:
        assert container.kernel.analytics_events.properties("note_created") == frozenset({"source", "length"})
        # The adapters built by the composition root share that registry.
        assert isinstance(container.kernel.analytics, NoopAnalytics)
    finally:
        container.close()

    monkeypatch.setattr(
        bootstrap.Container,
        "contributions",
        lambda self: [*real(self), contributed, Contribution(analytics_events=NOTE)],
    )
    with pytest.raises(ValueError, match="already registered"):
        build_container(make_settings())


def test_posthog_exports_a_registered_event_and_nothing_else() -> None:
    events = AnalyticsEvents()
    events.register(NOTE)
    with stub_server(lambda request: (200, {"status": 1})) as posthog:
        analytics = PostHogAnalytics(api_key="phc_test_only", host=posthog.url, events=events)
        try:
            analytics.capture(uuid.uuid4(), "note_created", {"source": "api", "title": "Secret plan"})
            analytics.capture(uuid.uuid4(), "note_deleted", {"source": "api"})
            assert analytics.flush()
        finally:
            analytics.shutdown()
        sent: list[dict[str, Any]] = [
            e for r in posthog.requests if r.path.startswith("/batch") for e in r.json()["batch"]
        ]
    assert [(e["event"], e["properties"].get("source"), "title" in e["properties"]) for e in sent] == [
        ("note_created", "api", False)
    ]
