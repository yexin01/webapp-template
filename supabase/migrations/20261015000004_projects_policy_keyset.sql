-- The projects access policy, reshaped so a list page reads one page of rows
-- (DB-02, API-03, DB-04). Who may read and write which rows is unchanged.
--
-- The first version was an OR of a personal and an organization branch, both
-- comparing columns with app.ctx_*(). The planner built index conditions from
-- both branches (a BitmapOr over the two page indexes) and, misestimating them,
-- fetched and sorted every row of the tenant on every page, including pages
-- reached with a keyset cursor. Choosing the branch with CASE on the context
-- leaves the policy a plain filter, so the query's own tenant predicate drives
-- the ordered page index and the scan stops at the page limit.
--
-- The policy therefore no longer narrows a scan by itself: every query on this
-- table must keep its own tenant predicate (org_id = ..., or owner_user_id = ...
-- AND org_id IS NULL), as the adapter does.

ALTER POLICY projects_api_access ON app.projects
  USING (
    CASE WHEN app.ctx_org_id() IS NULL
      THEN org_id IS NULL AND owner_user_id = app.ctx_user_id()
      ELSE org_id = app.ctx_org_id() AND EXISTS (
        SELECT 1 FROM app.organization_memberships m
        WHERE m.org_id = projects.org_id AND m.user_id = app.ctx_user_id())
    END
  )
  WITH CHECK (
    created_by = app.ctx_user_id() AND CASE WHEN app.ctx_org_id() IS NULL
      THEN org_id IS NULL AND owner_user_id = app.ctx_user_id()
      ELSE org_id = app.ctx_org_id() AND EXISTS (
        SELECT 1 FROM app.organization_memberships m
        WHERE m.org_id = projects.org_id AND m.user_id = app.ctx_user_id())
    END
  );
