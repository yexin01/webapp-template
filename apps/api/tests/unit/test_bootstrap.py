"""The composition root fails loudly where a test would otherwise run on something it did not mean to inject."""

from __future__ import annotations

import pytest

from app.bootstrap import build_container
from app.kernel.wiring import Overrides, pick
from app.modules.billing.adapters.fakes import InMemoryBillingProvider
from tests.conftest import make_settings


def test_a_misspelled_override_is_refused_by_name() -> None:
    """``billing_provder`` would be ignored, and the test would run on the settings' provider instead."""
    with pytest.raises(TypeError, match="billing_provder"):
        build_container(make_settings(), billing_provder=InMemoryBillingProvider())


def test_every_unknown_override_is_named() -> None:
    with pytest.raises(TypeError, match=r"analytcs, billing_provder$"):
        build_container(make_settings(), billing_provder=1, analytcs=2)


def test_known_overrides_build_a_container() -> None:
    container = build_container(make_settings(), billing_provider=InMemoryBillingProvider())
    try:
        assert container.billing.service is not None
        assert len(container.contributions()) == len(container.module_contributions()) + 1
    finally:
        container.close()


def test_overrides_report_only_what_no_builder_asked_for() -> None:
    overrides = Overrides({"clock": 1, "verifier": 2, "directory": None})
    assert pick(overrides, "clock", lambda: 0) == 1
    assert pick(overrides, "directory", lambda: 0) is None  # an explicit None is an override
    assert pick(overrides, "analytics", lambda: 3) == 3
    assert overrides.unread() == ["verifier"]


@pytest.mark.parametrize("name", ["billing_mod", "BILLING_MODDE"])
def test_a_misspelled_setting_is_refused_by_name(name: str) -> None:
    with pytest.raises(TypeError, match=name):
        make_settings(**{name: "sandbox"})
