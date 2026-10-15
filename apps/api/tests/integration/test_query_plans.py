"""DB-02: a projects list page reads one page through its page index.

``tests/query_plans.py`` reads the plan of the adapter's own list statement as
the request role, over equal-size tenants interleaved on disk.
"""

from __future__ import annotations

import psycopg
import pytest

from app.kernel.infrastructure.db import PgTransactions
from app.kernel.pagination import DEFAULT_PAGE_SIZE
from app.modules.projects.adapters import PgProjectRepository
from tests.conftest import Harness
from tests.query_plans import ORG_CONTEXT, PERSONAL_CONTEXT, halfway_cursor, page_plan, seed_tenants

pytestmark = pytest.mark.db

CASES = {
    "organization": (ORG_CONTEXT, "projects_org_page_idx"),
    "personal": (PERSONAL_CONTEXT, "projects_owner_page_idx"),
}


@pytest.fixture
def transactions(harness: Harness) -> PgTransactions[psycopg.Connection]:
    """The application's own transactions over the seeded tenants, whose repository is the connection itself."""
    seed_tenants(harness.database_url, "projects")
    return harness.container.kernel.transactions(lambda conn: conn)


@pytest.mark.parametrize("page", ["first", "deep"])
@pytest.mark.parametrize("context", ["organization", "personal"])
def test_list_reads_one_page_through_the_page_index(
    transactions: PgTransactions[psycopg.Connection], context: str, page: str
) -> None:
    """Whatever the tenant's size or the page's depth: Limit over an index scan that stops at the page size."""
    ctx, index = CASES[context]
    after = halfway_cursor(transactions, PgProjectRepository, ctx) if page == "deep" else None
    path = page_plan(transactions, PgProjectRepository, ctx, after)
    shape = [(node["Node Type"], node.get("Index Name")) for node in path]
    assert shape == [("Limit", None), ("Index Scan", index)], (shape, path[0])
    # The scan stopped at the page: no rows fetched beyond it, none discarded by the policy or the cursor.
    assert (path[1]["Actual Rows"], path[1].get("Rows Removed by Filter", 0)) == (DEFAULT_PAGE_SIZE, 0), path[0]
