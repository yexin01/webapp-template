-- One policy per command on projects, replacing the FOR ALL policy (DB-02, DB-07).
--
-- A FOR ALL policy applies its WITH CHECK to UPDATE as well, so its creator-only check let only a row's creator
-- update it, or anyone who also set created_by to themselves. The creator is checked on INSERT only now; UPDATE
-- keeps a row in the caller's tenant. Who is allowed to edit stays an application rule, as for delete. Who may
-- read, create, or delete which rows is unchanged.
--
-- app_api still has no UPDATE privilege on projects, because no route updates one. To let members edit, grant
-- UPDATE on the editable columns only: RLS cannot keep id, org_id, owner_user_id, created_by, or created_at
-- unchanged, and a column grant can.
--
-- As in 20261015000004, every query on this table must keep its own tenant predicate (org_id = ..., or
-- owner_user_id = ... AND org_id IS NULL): the policy is a filter and does not narrow a scan by itself.

DROP POLICY projects_api_access ON app.projects;

CREATE POLICY projects_api_select ON app.projects FOR SELECT TO app_api
  USING (
    CASE WHEN app.ctx_org_id() IS NULL
      THEN org_id IS NULL AND owner_user_id = app.ctx_user_id()
      ELSE org_id = app.ctx_org_id() AND EXISTS (
        SELECT 1 FROM app.organization_memberships m
        WHERE m.org_id = projects.org_id AND m.user_id = app.ctx_user_id())
    END
  );

CREATE POLICY projects_api_insert ON app.projects FOR INSERT TO app_api
  WITH CHECK (
    created_by = app.ctx_user_id() AND CASE WHEN app.ctx_org_id() IS NULL
      THEN org_id IS NULL AND owner_user_id = app.ctx_user_id()
      ELSE org_id = app.ctx_org_id() AND EXISTS (
        SELECT 1 FROM app.organization_memberships m
        WHERE m.org_id = projects.org_id AND m.user_id = app.ctx_user_id())
    END
  );

CREATE POLICY projects_api_update ON app.projects FOR UPDATE TO app_api
  USING (
    CASE WHEN app.ctx_org_id() IS NULL
      THEN org_id IS NULL AND owner_user_id = app.ctx_user_id()
      ELSE org_id = app.ctx_org_id() AND EXISTS (
        SELECT 1 FROM app.organization_memberships m
        WHERE m.org_id = projects.org_id AND m.user_id = app.ctx_user_id())
    END
  )
  WITH CHECK (
    CASE WHEN app.ctx_org_id() IS NULL
      THEN org_id IS NULL AND owner_user_id = app.ctx_user_id()
      ELSE org_id = app.ctx_org_id() AND EXISTS (
        SELECT 1 FROM app.organization_memberships m
        WHERE m.org_id = projects.org_id AND m.user_id = app.ctx_user_id())
    END
  );

CREATE POLICY projects_api_delete ON app.projects FOR DELETE TO app_api
  USING (
    CASE WHEN app.ctx_org_id() IS NULL
      THEN org_id IS NULL AND owner_user_id = app.ctx_user_id()
      ELSE org_id = app.ctx_org_id() AND EXISTS (
        SELECT 1 FROM app.organization_memberships m
        WHERE m.org_id = projects.org_id AND m.user_id = app.ctx_user_id())
    END
  );
