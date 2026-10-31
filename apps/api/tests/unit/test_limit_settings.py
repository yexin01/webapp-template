"""The settings behind the write limit and the job guard (SEC-09): their defaults and their bounds."""

from __future__ import annotations

import pytest

from app.kernel.infrastructure.config import ConfigError
from tests.conftest import make_settings


@pytest.mark.parametrize("value", [0, -1, 601])
def test_writes_per_user_per_minute_is_bounded(value: int) -> None:
    with pytest.raises(ConfigError, match="WRITES_PER_USER_PER_MINUTE"):
        make_settings(writes_per_user_per_minute=value)


def test_writes_per_user_per_minute_defaults_to_60_and_accepts_its_bounds() -> None:
    assert make_settings().writes_per_user_per_minute == 60
    assert make_settings(writes_per_user_per_minute=1).writes_per_user_per_minute == 1
    assert make_settings(writes_per_user_per_minute=600).writes_per_user_per_minute == 600
