-- Runtime role check (DB-06). The core schema creates app_api and app_worker
-- only when they are missing, so a role that already existed on the server
-- keeps the attributes and memberships it had. Stop while either one is a
-- superuser, has BYPASSRLS or LOGIN, owns a relation in schema app, or is a
-- member of a role that owns one or that a policy there is for, naming each
-- problem and its fix. Roles are only read: a hosted provider may not allow
-- changing them.

DO $$
DECLARE
  r record;
  owned text;
  grants text;
  problems text[] := '{}';
BEGIN
  FOR r IN
    SELECT oid, rolname, rolsuper, rolbypassrls, rolcanlogin
    FROM pg_roles WHERE rolname IN ('app_api', 'app_worker') ORDER BY rolname
  LOOP
    IF r.rolsuper THEN
      problems := array_append(problems, format('%I is a superuser (ALTER ROLE %1$I NOSUPERUSER)', r.rolname));
    END IF;
    IF r.rolbypassrls THEN
      problems := array_append(problems, format('%I has BYPASSRLS (ALTER ROLE %1$I NOBYPASSRLS)', r.rolname));
    END IF;
    IF r.rolcanlogin THEN
      problems := array_append(problems, format('%I has LOGIN (ALTER ROLE %1$I NOLOGIN)', r.rolname));
    END IF;

    -- Indexes, and the sequences behind identity and serial columns, always
    -- have their table's owner and move with it. A composite type holds no rows.
    SELECT string_agg(format('%I.%I', n.nspname, c.relname), ', ' ORDER BY c.relname) INTO owned
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'app' AND c.relowner = r.oid
      AND c.relkind NOT IN ('i', 'I', 'c')
      AND NOT (c.relkind = 'S' AND EXISTS (
        SELECT 1 FROM pg_depend d
        WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid
          AND d.refclassid = 'pg_class'::regclass AND d.deptype IN ('a', 'i')));
    IF owned IS NOT NULL THEN
      problems := array_append(problems,
        format('%I owns %s (ALTER TABLE <relation> OWNER TO %I)', r.rolname, owned, current_user));
    END IF;

    -- A member acts as the owner by inheriting its privileges, or without them by
    -- handing it a function to run. Name the direct memberships: they are what to revoke.
    SELECT string_agg(format('%I', g.rolname), ', ' ORDER BY g.rolname) INTO grants
    FROM pg_roles g
    WHERE EXISTS (SELECT 1 FROM pg_auth_members m WHERE m.roleid = g.oid AND m.member = r.oid)
      AND EXISTS (
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'app' AND c.relkind NOT IN ('i', 'I', 'c')
          AND pg_has_role(g.oid, c.relowner, 'MEMBER'));
    IF grants IS NOT NULL THEN
      problems := array_append(problems, format(
        '%I can act as the owner of relations in schema app through its membership in %s (REVOKE %2$s FROM %1$I)',
        r.rolname, grants));
    END IF;

    -- Members of a role can use its policies, and the worker's let every row through.
    SELECT string_agg(format('%I', g.rolname), ', ' ORDER BY g.rolname) INTO grants
    FROM pg_roles g
    WHERE EXISTS (SELECT 1 FROM pg_auth_members m WHERE m.roleid = g.oid AND m.member = r.oid)
      AND EXISTS (
        SELECT 1 FROM pg_policy p
        JOIN pg_class c ON c.oid = p.polrelid JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN LATERAL unnest(p.polroles) AS pr(oid)
        WHERE n.nspname = 'app' AND pr.oid NOT IN (0, r.oid)
          AND pg_has_role(g.oid, pr.oid, 'MEMBER'));
    IF grants IS NOT NULL THEN
      problems := array_append(problems, format(
        '%I can use the row-level security policies for other roles through its membership in %s '
        '(REVOKE %2$s FROM %1$I)',
        r.rolname, grants));
    END IF;
  END LOOP;

  IF cardinality(problems) > 0 THEN
    RAISE EXCEPTION 'unsafe runtime roles: %', array_to_string(problems, '; ')
      USING DETAIL = 'Row-level security does not apply to a superuser or to a role with BYPASSRLS. '
                     'The owner of a table can switch it off, and so can a member of the owner''s role. '
                     'A member of a role can also use the policies for that role. '
                     'The API enters these roles with SET ROLE; nothing logs in as them.',
            HINT = 'Fix each problem as shown, then run the migrations again. If another application '
                   'uses these roles, give this application a database server of its own.';
  END IF;
END
$$;
