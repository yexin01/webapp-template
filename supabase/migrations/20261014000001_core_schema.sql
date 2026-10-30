-- Core schema: identity mirror, tenancy, example resource, billing, audit,
-- webhook ledgers, and rate-limit counters.
--
-- Security model (DEC-008, ADR-0001):
--   * Tables live in schema "app", which is not exposed to Supabase client roles.
--   * The API switches every transaction to a NOLOGIN role:
--       app_api    - user requests; tenant-scoped by RLS
--       app_worker - webhooks, synchronization, maintenance
--   * RLS is enabled and FORCED on every table; neither role has BYPASSRLS
--     or owns tables.
--   * Context is read from transaction-local settings app.user_id,
--     app.org_id, and app.clerk_user_id.

CREATE SCHEMA IF NOT EXISTS app;
REVOKE ALL ON SCHEMA app FROM PUBLIC;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_api') THEN
    CREATE ROLE app_api NOLOGIN NOINHERIT NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_worker') THEN
    CREATE ROLE app_worker NOLOGIN NOINHERIT NOBYPASSRLS;
  END IF;
END
$$;

-- The connecting role must be able to SET ROLE into the runtime roles. The
-- grantee is named, not CURRENT_USER: GRANT ... TO CURRENT_USER crashes the
-- server on Supabase images older than 17.6.1.152 (supautils before 3.2.3).
DO $$
BEGIN
  EXECUTE format('GRANT app_api, app_worker TO %I', current_user);
END
$$;
GRANT USAGE ON SCHEMA app TO app_api, app_worker;

-- ---------------------------------------------------------------------------
-- Context helpers
-- ---------------------------------------------------------------------------

CREATE FUNCTION app.ctx_user_id() RETURNS uuid
  LANGUAGE sql STABLE
  AS $$ SELECT nullif(current_setting('app.user_id', true), '')::uuid $$;

CREATE FUNCTION app.ctx_org_id() RETURNS uuid
  LANGUAGE sql STABLE
  AS $$ SELECT nullif(current_setting('app.org_id', true), '')::uuid $$;

CREATE FUNCTION app.ctx_clerk_user_id() RETURNS text
  LANGUAGE sql STABLE
  AS $$ SELECT nullif(current_setting('app.clerk_user_id', true), '') $$;

-- ---------------------------------------------------------------------------
-- Tables
-- ---------------------------------------------------------------------------

CREATE TABLE app.users (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  clerk_user_id text NOT NULL UNIQUE CHECK (length(clerk_user_id) BETWEEN 1 AND 255),
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE app.organizations (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  clerk_org_id text NOT NULL UNIQUE CHECK (length(clerk_org_id) BETWEEN 1 AND 255),
  created_at   timestamptz NOT NULL DEFAULT now(),
  deleted_at   timestamptz
);

CREATE TABLE app.organization_memberships (
  org_id      uuid NOT NULL REFERENCES app.organizations (id) ON DELETE CASCADE,
  user_id     uuid NOT NULL REFERENCES app.users (id) ON DELETE CASCADE,
  role        text NOT NULL CHECK (role IN ('admin', 'member')),
  verified_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (org_id, user_id)
);
CREATE INDEX organization_memberships_user_idx ON app.organization_memberships (user_id);

-- Example tenant-owned resource: exactly one of org_id / owner_user_id is set.
CREATE TABLE app.projects (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        uuid REFERENCES app.organizations (id) ON DELETE RESTRICT,
  owner_user_id uuid REFERENCES app.users (id) ON DELETE RESTRICT,
  created_by    uuid NOT NULL REFERENCES app.users (id) ON DELETE RESTRICT,
  name          text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 120),
  created_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT projects_single_tenant CHECK ((org_id IS NULL) <> (owner_user_id IS NULL))
);
CREATE INDEX projects_org_page_idx ON app.projects (org_id, created_at DESC, id DESC) WHERE org_id IS NOT NULL;
CREATE INDEX projects_owner_page_idx ON app.projects (owner_user_id, created_at DESC, id DESC) WHERE owner_user_id IS NOT NULL;

CREATE TABLE app.billing_customers (
  user_id            uuid PRIMARY KEY REFERENCES app.users (id) ON DELETE RESTRICT,
  stripe_customer_id text NOT NULL UNIQUE CHECK (stripe_customer_id LIKE 'cus\_%'),
  created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE app.subscriptions (
  stripe_subscription_id text PRIMARY KEY CHECK (stripe_subscription_id LIKE 'sub\_%'),
  user_id                uuid NOT NULL REFERENCES app.users (id) ON DELETE RESTRICT,
  stripe_customer_id     text NOT NULL REFERENCES app.billing_customers (stripe_customer_id) ON DELETE RESTRICT,
  status                 text NOT NULL CHECK (status IN (
                           'incomplete', 'incomplete_expired', 'trialing', 'active',
                           'past_due', 'canceled', 'unpaid', 'paused')),
  plan_key               text NOT NULL CHECK (plan_key IN ('pro_monthly', 'pro_yearly', 'unknown')),
  price_id               text,
  current_period_end     timestamptz,
  cancel_at_period_end   boolean NOT NULL DEFAULT false,
  cancel_at              timestamptz,
  ended_at               timestamptz,
  synced_at              timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX subscriptions_user_idx ON app.subscriptions (user_id);
CREATE INDEX subscriptions_customer_idx ON app.subscriptions (stripe_customer_id);

-- Webhook ledgers. Full payloads are not stored (DEC-004).
CREATE TABLE app.billing_events (
  stripe_event_id    text PRIMARY KEY,
  event_type         text NOT NULL,
  stripe_customer_id text,
  event_created_at   timestamptz NOT NULL,
  received_at        timestamptz NOT NULL DEFAULT now(),
  processed_at       timestamptz,
  status             text NOT NULL CHECK (status IN ('received', 'processed', 'ignored', 'needs_reconciliation', 'failed')),
  attempts           integer NOT NULL DEFAULT 0
);
CREATE INDEX billing_events_status_idx ON app.billing_events (status) WHERE status <> 'processed';

CREATE TABLE app.identity_events (
  event_id     text PRIMARY KEY,
  event_type   text NOT NULL,
  received_at  timestamptz NOT NULL DEFAULT now(),
  processed_at timestamptz,
  status       text NOT NULL CHECK (status IN ('received', 'processed', 'ignored', 'needs_reconciliation', 'failed')),
  attempts     integer NOT NULL DEFAULT 0
);

CREATE TABLE app.audit_log (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  occurred_at   timestamptz NOT NULL DEFAULT now(),
  actor_user_id uuid REFERENCES app.users (id) ON DELETE SET NULL,
  org_id        uuid REFERENCES app.organizations (id) ON DELETE SET NULL,
  action        text NOT NULL,
  target_type   text,
  target_id     text,
  outcome       text NOT NULL CHECK (outcome IN ('allowed', 'denied', 'succeeded', 'failed')),
  request_id    text
);
CREATE INDEX audit_log_org_idx ON app.audit_log (org_id, occurred_at DESC);

CREATE TABLE app.rate_limit_counters (
  bucket_key   text NOT NULL,
  window_start timestamptz NOT NULL,
  hits         integer NOT NULL DEFAULT 0,
  PRIMARY KEY (bucket_key, window_start)
);
CREATE INDEX rate_limit_counters_window_idx ON app.rate_limit_counters (window_start);

-- ---------------------------------------------------------------------------
-- Row-level security
-- ---------------------------------------------------------------------------

ALTER TABLE app.users                    ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.organizations            ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.organization_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.projects                 ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.billing_customers        ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.subscriptions            ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.billing_events           ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.identity_events          ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.audit_log                ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.rate_limit_counters      ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.users                    FORCE ROW LEVEL SECURITY;
ALTER TABLE app.organizations            FORCE ROW LEVEL SECURITY;
ALTER TABLE app.organization_memberships FORCE ROW LEVEL SECURITY;
ALTER TABLE app.projects                 FORCE ROW LEVEL SECURITY;
ALTER TABLE app.billing_customers        FORCE ROW LEVEL SECURITY;
ALTER TABLE app.subscriptions            FORCE ROW LEVEL SECURITY;
ALTER TABLE app.billing_events           FORCE ROW LEVEL SECURITY;
ALTER TABLE app.identity_events          FORCE ROW LEVEL SECURITY;
ALTER TABLE app.audit_log                FORCE ROW LEVEL SECURITY;
ALTER TABLE app.rate_limit_counters      FORCE ROW LEVEL SECURITY;

-- users: a request may see and create only its own user row.
GRANT SELECT, INSERT ON app.users TO app_api;
CREATE POLICY users_api_select ON app.users FOR SELECT TO app_api
  USING (clerk_user_id = app.ctx_clerk_user_id() OR id = app.ctx_user_id());
CREATE POLICY users_api_insert ON app.users FOR INSERT TO app_api
  WITH CHECK (clerk_user_id = app.ctx_clerk_user_id());

-- organizations / memberships: read-only for requests, own memberships only.
GRANT SELECT ON app.organizations TO app_api;
CREATE POLICY organizations_api_select ON app.organizations FOR SELECT TO app_api
  USING (EXISTS (
    SELECT 1 FROM app.organization_memberships m
    WHERE m.org_id = organizations.id AND m.user_id = app.ctx_user_id()));

GRANT SELECT ON app.organization_memberships TO app_api;
CREATE POLICY memberships_api_select ON app.organization_memberships FOR SELECT TO app_api
  USING (user_id = app.ctx_user_id());

-- projects: personal rows in personal context; organization rows only in that
-- organization's context and only for its members.
GRANT SELECT, INSERT, DELETE ON app.projects TO app_api;
CREATE POLICY projects_api_access ON app.projects FOR ALL TO app_api
  USING (
    (org_id IS NULL AND app.ctx_org_id() IS NULL AND owner_user_id = app.ctx_user_id())
    OR (org_id IS NOT NULL AND org_id = app.ctx_org_id() AND EXISTS (
          SELECT 1 FROM app.organization_memberships m
          WHERE m.org_id = projects.org_id AND m.user_id = app.ctx_user_id()))
  )
  WITH CHECK (
    created_by = app.ctx_user_id() AND (
      (org_id IS NULL AND app.ctx_org_id() IS NULL AND owner_user_id = app.ctx_user_id())
      OR (org_id IS NOT NULL AND org_id = app.ctx_org_id() AND EXISTS (
            SELECT 1 FROM app.organization_memberships m
            WHERE m.org_id = projects.org_id AND m.user_id = app.ctx_user_id())))
  );

-- billing: requests read their own records and may create their own customer link.
GRANT SELECT, INSERT ON app.billing_customers TO app_api;
CREATE POLICY billing_customers_api_select ON app.billing_customers FOR SELECT TO app_api
  USING (user_id = app.ctx_user_id());
CREATE POLICY billing_customers_api_insert ON app.billing_customers FOR INSERT TO app_api
  WITH CHECK (user_id = app.ctx_user_id());

GRANT SELECT ON app.subscriptions TO app_api;
CREATE POLICY subscriptions_api_select ON app.subscriptions FOR SELECT TO app_api
  USING (user_id = app.ctx_user_id());

-- audit: requests may append entries attributed to themselves; never read.
GRANT INSERT ON app.audit_log TO app_api;
CREATE POLICY audit_api_insert ON app.audit_log FOR INSERT TO app_api
  WITH CHECK (actor_user_id = app.ctx_user_id());

-- Worker role: system processes (webhooks, synchronization, maintenance).
GRANT SELECT, INSERT, UPDATE ON app.users TO app_worker;
GRANT SELECT, INSERT, UPDATE ON app.organizations TO app_worker;
GRANT SELECT, INSERT, UPDATE, DELETE ON app.organization_memberships TO app_worker;
GRANT SELECT ON app.projects TO app_worker;
GRANT SELECT, INSERT ON app.billing_customers TO app_worker;
GRANT SELECT, INSERT, UPDATE ON app.subscriptions TO app_worker;
GRANT SELECT, INSERT, UPDATE ON app.billing_events TO app_worker;
GRANT SELECT, INSERT, UPDATE ON app.identity_events TO app_worker;
GRANT SELECT, INSERT ON app.audit_log TO app_worker;
GRANT SELECT, INSERT, UPDATE, DELETE ON app.rate_limit_counters TO app_worker;

CREATE POLICY users_worker ON app.users FOR ALL TO app_worker USING (true) WITH CHECK (true);
CREATE POLICY organizations_worker ON app.organizations FOR ALL TO app_worker USING (true) WITH CHECK (true);
CREATE POLICY memberships_worker ON app.organization_memberships FOR ALL TO app_worker USING (true) WITH CHECK (true);
CREATE POLICY projects_worker_select ON app.projects FOR SELECT TO app_worker USING (true);
CREATE POLICY billing_customers_worker ON app.billing_customers FOR ALL TO app_worker USING (true) WITH CHECK (true);
CREATE POLICY subscriptions_worker ON app.subscriptions FOR ALL TO app_worker USING (true) WITH CHECK (true);
CREATE POLICY billing_events_worker ON app.billing_events FOR ALL TO app_worker USING (true) WITH CHECK (true);
CREATE POLICY identity_events_worker ON app.identity_events FOR ALL TO app_worker USING (true) WITH CHECK (true);
CREATE POLICY audit_worker ON app.audit_log FOR ALL TO app_worker USING (true) WITH CHECK (true);
CREATE POLICY rate_limits_worker ON app.rate_limit_counters FOR ALL TO app_worker USING (true) WITH CHECK (true);

-- ---------------------------------------------------------------------------
-- Membership lock used before privileged writes (TEN-07).
-- Runs as app_worker so it can take FOR SHARE without granting app_api
-- UPDATE rights on memberships; it only ever locks the caller's own row.
-- ---------------------------------------------------------------------------

CREATE FUNCTION app.lock_my_membership(p_org_id uuid) RETURNS text
  LANGUAGE plpgsql SECURITY DEFINER
  SET search_path = ''
  AS $$
DECLARE
  v_role text;
BEGIN
  SELECT m.role INTO v_role
  FROM app.organization_memberships m
  JOIN app.organizations o ON o.id = m.org_id AND o.deleted_at IS NULL
  WHERE m.org_id = p_org_id AND m.user_id = app.ctx_user_id()
  FOR SHARE OF m;
  RETURN v_role;
END
$$;
-- Changing ownership requires the new owner to hold CREATE on the schema when
-- the migration does not run as a superuser (as on hosted Supabase).
GRANT CREATE ON SCHEMA app TO app_worker;
ALTER FUNCTION app.lock_my_membership(uuid) OWNER TO app_worker;
REVOKE CREATE ON SCHEMA app FROM app_worker;
REVOKE ALL ON FUNCTION app.lock_my_membership(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.lock_my_membership(uuid) TO app_api;
