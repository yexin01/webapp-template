"""Startup checks of the scheduled-job configuration (ARCH-16)."""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

import pytest
from pydantic import SecretStr

from app.bootstrap import create_app
from app.kernel.infrastructure.config import AppEnv
from tests.conftest import Harness

pytestmark = pytest.mark.db


def jobs_warnings(harness: Harness, caplog: pytest.LogCaptureFixture, **settings: Any) -> list[logging.LogRecord]:
    container = dataclasses.replace(harness.container, settings=harness.container.settings.model_copy(update=settings))
    with caplog.at_level(logging.WARNING, logger="app.bootstrap"):
        create_app(container)
    return [r for r in caplog.records if r.getMessage() == "jobs_endpoint_disabled"]


def test_production_without_a_cron_secret_warns_that_nothing_schedules_jobs(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    """Not an error, since jobs can run from the CLI; but otherwise the outbox is never drained or pruned."""
    [warning] = jobs_warnings(harness, caplog, app_env=AppEnv.PRODUCTION, cron_secret=None)
    assert {"drain-outbox", "prune-expired"} <= set(warning.jobs.split(","))  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "settings",
    [
        {"app_env": AppEnv.PRODUCTION, "cron_secret": SecretStr("s" * 32)},
        {"app_env": AppEnv.PREVIEW, "cron_secret": None},
        {"app_env": AppEnv.TEST, "cron_secret": None},
    ],
    ids=["production-with-secret", "preview", "test"],
)
def test_no_warning_otherwise(harness: Harness, caplog: pytest.LogCaptureFixture, settings: dict[str, Any]) -> None:
    assert jobs_warnings(harness, caplog, **settings) == []
