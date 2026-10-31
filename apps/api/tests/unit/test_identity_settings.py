"""Identity configuration bounds."""

from __future__ import annotations

import base64

import pytest

from app.kernel.infrastructure.config import ConfigError
from app.modules.identity.adapters.clerk import ClerkWebhookVerifier
from tests.conftest import make_settings


def test_membership_staleness_is_capped_at_the_approved_bound() -> None:
    """TEN-07 fixes the bound at 300 s (DEC-009, ADR-0002); a shorter bound is allowed, a longer one is not."""
    assert make_settings().membership_max_staleness_seconds == 300
    assert make_settings(membership_max_staleness_seconds=60).membership_max_staleness_seconds == 60
    with pytest.raises(ConfigError, match="MEMBERSHIP_MAX_STALENESS_SECONDS"):
        make_settings(membership_max_staleness_seconds=301)


def test_clerk_reads_in_flight_stay_well_below_the_request_threads() -> None:
    """At least a read and the two requests that may always wait for it, so a page's parallel requests share it."""
    assert make_settings().clerk_max_concurrent_reads == 8
    for value in (3, 16):
        assert make_settings(clerk_max_concurrent_reads=value).clerk_max_concurrent_reads == value
    for value in (2, 17):
        with pytest.raises(ConfigError, match="CLERK_MAX_CONCURRENT_READS"):
            make_settings(clerk_max_concurrent_reads=value)


@pytest.mark.parametrize(
    "value",
    [
        "whsec_CANARY123",  # a truncated key: no whole number of bytes
        "whsec_",
        "whsec_CANARY!!!",  # not base64
        "whsec_CANARY#comment",  # svix would silently drop the "#" and verify with another key
        "whsec_CANARY12 CANARY12",  # only surrounding whitespace is a paste artifact
        "CANARY12",  # no prefix
    ],
)
def test_a_malformed_webhook_signing_secret_is_named_at_startup(value: str) -> None:
    """ENV-03: the variable is named, its value never shown, instead of a decoding error at container build."""
    with pytest.raises(ConfigError, match="CLERK_WEBHOOK_SIGNING_SECRET") as excinfo:
        make_settings(clerk_webhook_signing_secret=value)
    assert "CANARY" not in str(excinfo.value)


@pytest.mark.parametrize(
    "value",
    [
        "whsec_" + base64.b64encode(b"k" * 24).decode(),  # as Clerk shows it: no padding
        "whsec_" + base64.b64encode(b"test-only-clerk-signing-secret!!").decode(),  # padded
        "whsec_" + base64.b64encode(b"k" * 24).decode() + "\n",  # pasted with its line break
        "  whsec_" + base64.b64encode(b"k" * 24).decode() + " ",
    ],
)
def test_a_well_formed_webhook_signing_secret_is_accepted(value: str) -> None:
    settings = make_settings(clerk_webhook_signing_secret=value)
    assert settings.clerk_webhook_signing_secret is not None
    # svix accepts it too, trimmed as the module passes it.
    ClerkWebhookVerifier(settings.clerk_webhook_signing_secret.get_secret_value().strip())


def test_the_token_audience_is_optional_and_trimmed() -> None:
    """AUTH-01: unset (or empty) means `aud` is not checked; a value pasted with spaces around it is trimmed."""
    assert make_settings().clerk_audience is None
    assert make_settings(clerk_audience="").clerk_audience is None
    assert make_settings(clerk_audience="   ").clerk_audience is None
    assert make_settings(clerk_audience="  https://api.example.com \n").clerk_audience == "https://api.example.com"
