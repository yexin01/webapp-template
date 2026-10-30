-- Run after a logical restore (pg_restore --no-owner), as the role that restored: the migration role.
--
-- A restore must not recreate owners: on a server where the restoring role is not a superuser (Supabase's
-- postgres), ALTER ... OWNER to app_worker is refused, because app_worker lacks CREATE on schema app, which a dump
-- records only in its final state (no CREATE). Everything is owned by the restoring role instead, and this file
-- gives the one object that must not be owned by it back to app_worker, as 20261014000001_core_schema.sql does:
-- app.lock_my_membership is SECURITY DEFINER, and runs with its owner's rights.
--
-- Keep it in step with the migrations: scripts/restore-drill.mjs compares owners and privileges after running it,
-- so an object a later migration hands to another role fails the drill until it is added here.

GRANT CREATE ON SCHEMA app TO app_worker;
ALTER FUNCTION app.lock_my_membership(uuid) OWNER TO app_worker;
REVOKE CREATE ON SCHEMA app FROM app_worker;
