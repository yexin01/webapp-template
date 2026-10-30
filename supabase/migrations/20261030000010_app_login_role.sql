-- The role the API logs in as (DEC-008, MIG-04). It owns nothing and holds no privileges of its own: its only
-- use is SET ROLE into app_api or app_worker, as every request transaction does. Migrations run as a different
-- role (MIGRATION_DATABASE_URL), the owner of the schema, so a leaked runtime credential cannot change it.
--
-- Created without LOGIN or a password, so a migration holds no secret. `pnpm db:api-login` sets both, with a
-- verifier computed on the client. NOINHERIT keeps a query that does not switch role from reaching app_api's or
-- app_worker's privileges, and the policies for those roles.
--
-- app_login can still SET ROLE app_worker, whose policies let every row through. This keeps the login from being
-- the table owner or a BYPASSRLS role; it does not stop code that runs arbitrary SQL from reading across tenants.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_login') THEN
    BEGIN
      CREATE ROLE app_login NOLOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB NOREPLICATION;
    EXCEPTION WHEN duplicate_object OR unique_violation THEN
      NULL; -- created by a migration run on another database of this server
    END;
  END IF;
END
$$;

-- Named roles, never CURRENT_USER (see the first migration).
GRANT app_api, app_worker TO app_login;
