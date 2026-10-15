"""Identity configuration bounds."""

from __future__ import annotations

import pytest

from app.kernel.infrastructure.config import ConfigError
from tests.conftest import make_settings


def test_membership_staleness_is_capped_at_the_approved_bound() -> None:
    """TEN-07 fixes the bound at 300 s (DEC-009, ADR-0002); a shorter bound is allowed, a longer one is not."""
    assert make_settings().membership_max_staleness_seconds == 300
    assert make_settings(membership_max_staleness_seconds=60).membership_max_staleness_seconds == 60
    with pytest.raises(ConfigError, match="MEMBERSHIP_MAX_STALENESS_SECONDS"):
        make_settings(membership_max_staleness_seconds=301)
