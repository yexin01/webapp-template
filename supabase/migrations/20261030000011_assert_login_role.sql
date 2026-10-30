-- Login role check (DB-06, DEC-008). The previous migration creates app_login only when it is missing, so a
-- role that already existed on the server keeps the attributes and memberships it had. Stop while it is a
-- superuser, has BYPASSRLS, CREATEROLE, or REPLICATION, inherits its memberships, holds privileges of its own
-- in schema app, or is a member of any role but app_api and app_worker. LOGIN is expected once `pnpm db:api-login`
-- ran. Those two roles' own attributes and memberships are checked by 20261015000006. Roles are only read: a
-- hosted provider may not allow changing them.

DO $$
DECLARE
  r record;
  direct text;
  grants text;
  problems text[] := '{}';
BEGIN
  FOR r IN
    SELECT oid, rolsuper, rolbypassrls, rolcreaterole, rolreplication, rolinherit
    FROM pg_roles WHERE rolname = 'app_login'
  LOOP
    IF r.rolsuper THEN
      problems := array_append(problems, 'app_login is a superuser (ALTER ROLE app_login NOSUPERUSER)');
    END IF;
    IF r.rolbypassrls THEN
      problems := array_append(problems, 'app_login has BYPASSRLS (ALTER ROLE app_login NOBYPASSRLS)');
    END IF;
    IF r.rolcreaterole THEN
      problems := array_append(problems, 'app_login has CREATEROLE (ALTER ROLE app_login NOCREATEROLE)');
    END IF;
    IF r.rolreplication THEN
      problems := array_append(problems, 'app_login has REPLICATION (ALTER ROLE app_login NOREPLICATION)');
    END IF;
    IF r.rolinherit THEN
      problems := array_append(problems, 'app_login inherits its memberships (ALTER ROLE app_login NOINHERIT)');
    END IF;

    -- Privileges granted to the role itself, not the ones it could reach through SET ROLE.
    SELECT string_agg(name, ', ' ORDER BY name) INTO direct FROM (
      SELECT format('%I.%I', n.nspname, c.relname) AS name
      FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
      CROSS JOIN LATERAL aclexplode(c.relacl) a
      WHERE n.nspname = 'app' AND a.grantee = r.oid
      UNION
      SELECT format('%I.%I()', n.nspname, p.proname)
      FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
      CROSS JOIN LATERAL aclexplode(p.proacl) a
      WHERE n.nspname = 'app' AND a.grantee = r.oid
      UNION
      SELECT format('schema %I', n.nspname)
      FROM pg_namespace n CROSS JOIN LATERAL aclexplode(n.nspacl) a
      WHERE n.nspname = 'app' AND a.grantee = r.oid
    ) x;
    IF direct IS NOT NULL THEN
      problems := array_append(problems, format(
        'app_login holds privileges of its own on %s (REVOKE ALL ON <object> FROM app_login)', direct));
    END IF;

    SELECT string_agg(format('%I', g.rolname), ', ' ORDER BY g.rolname) INTO grants
    FROM pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid
    WHERE m.member = r.oid AND g.rolname NOT IN ('app_api', 'app_worker');
    IF grants IS NOT NULL THEN
      problems := array_append(problems, format(
        'app_login is a member of %s, which it may not be (REVOKE %1$s FROM app_login)', grants));
    END IF;
  END LOOP;

  IF cardinality(problems) > 0 THEN
    RAISE EXCEPTION 'unsafe login role: %', array_to_string(problems, '; ')
      USING DETAIL = 'The API logs in as app_login and switches to app_api or app_worker for every transaction. '
                     'A login role that is a superuser, has BYPASSRLS, owns tables, or holds privileges of its own '
                     'lets a query that does not switch role skip row-level security.',
            HINT = 'Fix each problem as shown, then run the migrations again. If another application '
                   'uses a role named app_login, give this application a database server of its own.';
  END IF;
END
$$;
