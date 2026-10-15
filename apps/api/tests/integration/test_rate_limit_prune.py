"""The prune-expired job deletes only rate-limit windows past retention (DEC-011, LLM-06).

Live windows hold the per-user limits and the daily budgets: deleting one would
let the job reset a limit or budget every time it runs.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import pytest

from tests.conftest import Harness, superuser_query

pytestmark = pytest.mark.db

CRON_SECRET = "cron-secret-test_only-prune-0123456789abcd"  # test-only value
RETENTION = timedelta(days=2)  # longer than the longest window (a day)


def test_prune_deletes_only_windows_older_than_its_cutoff(make_harness: Callable[..., Harness]) -> None:
    h = make_harness({"cron_secret": CRON_SECRET, "llm_checks_global_per_day": 1})
    headers = h.login("user_a")
    assert h.client.post("/api/v1/llm/check", headers=headers).status_code == 200  # spends the day's global budget
    [(user_id,)] = superuser_query(h.database_url, "SELECT id FROM app.users WHERE clerk_user_id = 'user_a'")
    cutoff = h.clock.now() - RETENTION
    for name, window_start in (
        ("expired", cutoff - timedelta(seconds=1)),
        ("at_cutoff", cutoff),
        ("recent", cutoff + timedelta(seconds=1)),
    ):
        superuser_query(
            h.database_url,
            "INSERT INTO app.rate_limit_counters (bucket_key, window_start, hits) VALUES (%s, %s, 1)",
            (f"test:{name}", window_start),
        )

    r = h.client.get("/api/v1/jobs/prune-expired", headers={"Authorization": f"Bearer {CRON_SECRET}"})
    assert r.status_code == 200, r.text
    assert r.json()["summary"]["rate_limit_windows"] == 1
    remaining = {key for (key,) in superuser_query(h.database_url, "SELECT bucket_key FROM app.rate_limit_counters")}
    assert remaining == {"llm:global", f"llm:user:{user_id}", "test:at_cutoff", "test:recent"}

    # The day's budget survived the job: the next check is still refused.
    refused = h.client.post("/api/v1/llm/check", headers=headers)
    assert (refused.status_code, refused.json()["code"]) == (429, "BUDGET_EXCEEDED")
