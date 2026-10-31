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
    PERSONAL_DATA_WORDS,
    AnalyticsEvents,
    NoopAnalytics,
    PostHogAnalytics,
    _filter,
)
from app.kernel.infrastructure.redaction import SENSITIVE_KEYS
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


@pytest.mark.parametrize(
    "prop",
    [
        # Named like the exact names the guard knew, with another word around them.
        "user_email",
        "email_address",
        "customer_email",
        "phone_number",
        "api_token",
        "client_secret",
        "password_hash",
        # Personal data it did not know at all.
        "name",
        "first_name",
        "last_name",
        "full_name",
        "firstname",
        "username",
        "ip_address",
        "client_ip",
        "address",
        "billing_address",
        "birthday",
        "date_of_birth",
        "dob",
        "ssn",
        # Text a user wrote.
        "message",
        "error_message",
        # Written in capitals too, where the name is also refused as malformed.
        "User_Email",
    ],
)
def test_a_property_with_a_personal_data_word_in_its_name_is_refused(prop: str) -> None:
    registry = AnalyticsEvents()
    with pytest.raises(ValueError, match=f"property '{prop}' is named like personal data or a secret"):
        registry.register({"signed_up": frozenset({prop})})
    assert registry.properties("signed_up") is None


# What a property name may not hold, as a policy: a word taken out makes the guard accept names it refused, so it is a
# decision to make here as well. A prefix that is no word of the policy and no sensitive key isolates the word.
POLICY_WORDS = (
    # who someone is, and how to reach them
    *("email", "phone", "mobile", "name", "firstname", "lastname", "fullname", "surname", "username", "nickname"),
    *("address", "street", "zip", "zipcode", "postcode", "postal", "ip", "ipaddress"),
    *("birth", "birthday", "birthdate", "dob", "ssn", "passport", "iban"),
    # credentials
    *("password", "passwd", "secret", "token", "cookie", "jwt", "apikey"),
    # what a user wrote
    *("prompt", "message", "messages", "body"),
)


@pytest.mark.parametrize("word", POLICY_WORDS)
def test_each_word_of_the_policy_refuses_a_property_that_holds_it(word: str) -> None:
    prop = f"record_{word}"
    assert prop not in SENSITIVE_KEYS
    registry = AnalyticsEvents()
    with pytest.raises(ValueError, match=f"property '{prop}' is named like personal data or a secret"):
        registry.register({"signed_up": frozenset({prop})})
    assert registry.properties("signed_up") is None


def test_the_policy_is_these_words_and_no_others() -> None:
    assert set(POLICY_WORDS) == PERSONAL_DATA_WORDS


@pytest.mark.parametrize(
    "prop",
    [
        # Properties the template and a module may well send.
        *("plan", "context", "source", "provider", "format", "length", "plan_key", "project_count"),
        "is_first_visit",
        # Letters of a personal data word inside another word.
        *("ipsum", "recipe", "shipping_method", "nameplate", "usernames", "emailed"),
    ],
)
def test_a_property_is_refused_for_a_word_of_its_name_not_for_letters_inside_one(prop: str) -> None:
    registry = AnalyticsEvents()
    registry.register({"signed_up": frozenset({prop})})
    assert registry.properties("signed_up") == frozenset({prop})


def test_none_of_the_kernels_own_properties_is_named_like_personal_data() -> None:
    for event, properties in ANALYTICS_ALLOWLIST.items():
        for prop in properties:
            assert prop not in SENSITIVE_KEYS, (event, prop)
            assert PERSONAL_DATA_WORDS.isdisjoint(prop.split("_")), (event, prop)


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
