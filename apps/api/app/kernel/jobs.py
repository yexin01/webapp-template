"""Scheduled and on-demand maintenance jobs.

Modules register jobs at composition. The same registry backs the CLI
(``python -m app.cli job <name>``) and the secret-protected HTTP endpoint that
Vercel Cron calls (``GET /api/v1/jobs/<name>``). A job must be safe to run
repeatedly, or concurrently with itself, and must bound its own work per run.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass

_NAME = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
_CRON = re.compile(r"^(\S+\s+){4}\S+$")

JobSummary = Mapping[str, int | str | bool]


@dataclass(frozen=True, slots=True)
class Job:
    name: str
    description: str
    schedule: str  # five-field cron expression, UTC
    run: Callable[[], JobSummary]

    def __post_init__(self) -> None:
        if not _NAME.fullmatch(self.name):
            raise ValueError(f"invalid job name: {self.name!r}")
        if not _CRON.fullmatch(self.schedule):
            raise ValueError(f"schedule must be a five-field cron expression: {self.schedule!r}")


class JobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def register(self, job: Job) -> None:
        if job.name in self._jobs:
            raise ValueError(f"job {job.name!r} is already registered")
        self._jobs[job.name] = job

    def get(self, name: str) -> Job | None:
        return self._jobs.get(name)

    def all(self) -> list[Job]:
        return [self._jobs[name] for name in sorted(self._jobs)]
