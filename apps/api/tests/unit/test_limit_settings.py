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


@pytest.mark.parametrize("value", [-1, 3_601])
def test_job_min_spacing_is_bounded(value: int) -> None:
    with pytest.raises(ConfigError, match="JOB_MIN_SPACING_SECONDS"):
        make_settings(job_min_spacing_seconds=value)


def test_job_min_spacing_defaults_to_60_and_zero_turns_it_off() -> None:
    assert make_settings().job_min_spacing_seconds == 60
    assert make_settings(job_min_spacing_seconds=0).job_min_spacing_seconds == 0
    assert make_settings(job_min_spacing_seconds=3_600).job_min_spacing_seconds == 3_600
