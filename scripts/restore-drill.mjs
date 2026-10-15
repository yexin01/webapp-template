#!/usr/bin/env node
/**
 * Local backup-and-restore drill (supports DEC-005; does NOT resolve it).
 *
 * Dumps the local Docker database and restores it into a FRESH PostgreSQL
 * server: a throwaway container from the same image, with no network, that
 * has never run a migration. Roles are cluster-wide and not part of a
 * database dump, so the drill first creates the runtime roles exactly as the
 * first migration does (ROLES_SQL), then restores with object owners intact.
 * It verifies per-table row counts and content checksums, row-level-security
 * flags, owners, privileges, policies, role attributes, and the migration
 * ledger, then removes the container. Prints timings as JSON.
 *
 * It touches only local Docker: it reads the docker-compose PostgreSQL
 * service (pg_dump, plus a temporary dump file inside that container) and
 * runs one throwaway container. It removes both on PASS, on FAIL, and after
 * SIGINT, SIGTERM, or SIGHUP (not after SIGKILL). Exit status: 0 on PASS,
 * 1 on FAIL, 128 + the signal number when a signal stopped it.
 *
 *   pnpm db:restore-drill
 *
 * This proves the dump format and the restore procedure for this schema,
 * including into a server where the roles do not exist yet. It is not
 * evidence about a hosted provider's backups, recovery objectives, or
 * point-in-time recovery (spec §8.4).
 */
import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { constants } from "node:os";
import path from "node:path";
import { setTimeout as sleep } from "node:timers/promises";

const root = path.resolve(import.meta.dirname, "..");
const DB = "app";
const DUMP = "/tmp/restore-drill.dump";
const TARGET = `restore-drill-${process.pid}-${randomBytes(4).toString("hex")}`;
const READY_TIMEOUT_MS = 60_000;

// Mirrors the role block of supabase/migrations/20261014000001_core_schema.sql.
// Keep the two in step; the _roles fingerprint below fails the drill if the
// attributes or memberships drift from what the migrations produce.
const ROLES_SQL = `
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
GRANT app_api TO CURRENT_USER;
GRANT app_worker TO CURRENT_USER;
`;

// Steps run asynchronously, so a signal is handled when it arrives: it aborts
// the running step (killing that docker CLI process) and skips the rest,
// cleanup still runs, and the drill exits with 128 + the signal number.
const abort = new AbortController();
let interrupted = null;
for (const signal of ["SIGINT", "SIGTERM", "SIGHUP"]) {
  process.on(signal, () => {
    interrupted ??= signal;
    abort.abort();
  });
}

/**
 * Runs a command; resolves with its stdout (a string, or a Buffer with
 * `binary`). A signal aborts it, unless it is `uninterruptible`: steps that
 * create something (the dump file, the container) and cleanup run to the end
 * in their own process group, out of reach of the terminal's Ctrl-C, so that
 * when they return the thing either exists or not, and cleanup can see it.
 */
function run(command, args, { input, binary = false, env, uninterruptible = false } = {}) {
  return new Promise((resolve, reject) => {
    if (!uninterruptible && abort.signal.aborted) {
      reject(new Error(`interrupted by ${interrupted}`));
      return;
    }
    const child = spawn(command, args, {
      cwd: root,
      env,
      stdio: [input === undefined ? "ignore" : "pipe", "pipe", "pipe"],
      detached: uninterruptible,
      signal: uninterruptible ? undefined : abort.signal,
    });
    const stdout = [];
    const stderr = [];
    child.stdout.on("data", (chunk) => stdout.push(chunk));
    child.stderr.on("data", (chunk) => stderr.push(chunk));
    child.on("error", reject);
    child.on("close", (code, signal) => {
      if (code === 0) {
        const output = Buffer.concat(stdout);
        resolve(binary ? output : output.toString("utf8"));
        return;
      }
      // Only the first arguments: never echo a whole command line.
      const error = new Error(`${command} ${args.slice(0, 2).join(" ")} failed (${signal ?? `exit ${code}`})`);
      error.stderr = Buffer.concat(stderr).toString("utf8");
      reject(error);
    });
    if (input !== undefined) {
      child.stdin.on("error", () => {}); // a command that stops reading fails through "close"
      child.stdin.end(input);
    }
  });
}

/** Runs a command in the docker-compose PostgreSQL service (the source). */
const source = (args, options) => run("docker", ["compose", "exec", "-T", "postgres", ...args], options);
/** Runs a command in the throwaway container (the target). */
const target = (args, options = {}) =>
  run("docker", ["exec", ...(options.input === undefined ? [] : ["--interactive"]), TARGET, ...args], options);

const psql = async (exec, sql) =>
  (await exec(["psql", "-U", "postgres", "-d", DB, "-v", "ON_ERROR_STOP=1", "-At", "-c", sql])).trim();

async function fingerprint(exec) {
  const tables = (await psql(exec, "SELECT tablename FROM pg_tables WHERE schemaname = 'app' ORDER BY 1"))
    .split("\n")
    .filter(Boolean);
  const result = {};
  for (const table of tables) {
    // Table names come from the catalog of a local database, not from input.
    const [count, digest] = (
      await psql(exec, `SELECT count(*), coalesce(md5(string_agg(t::text, '|' ORDER BY t::text)), '-') FROM app."${table}" t`)
    ).split("|");
    result[table] = { rows: Number(count), md5: digest };
  }
  result._rls = await psql(
    exec,
    "SELECT string_agg(c.relname || ':' || c.relrowsecurity || ':' || c.relforcerowsecurity, ',' ORDER BY c.relname) " +
      "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'app' AND c.relkind = 'r'",
  );
  // Grants on every relation and function in schema app, and on the schema.
  const acl = (column) => `coalesce((SELECT string_agg(a::text, ' ' ORDER BY a::text) FROM unnest(${column}) a), '-')`;
  result._privileges = await psql(
    exec,
    "SELECT string_agg(name || '=' || acl, ',' ORDER BY name) FROM (" +
      `SELECT c.relname::text AS name, ${acl("c.relacl")} AS acl FROM pg_class c ` +
      "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'app' " +
      `UNION ALL SELECT p.oid::regprocedure::text, ${acl("p.proacl")} FROM pg_proc p ` +
      "JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'app' " +
      `UNION ALL SELECT 'schema app', ${acl("nspacl")} FROM pg_namespace WHERE nspname = 'app') x`,
  );
  // Owners matter: app.lock_my_membership is SECURITY DEFINER and must stay
  // owned by app_worker, not by whichever role ran the restore.
  result._owners = await psql(
    exec,
    "SELECT string_agg(name || '=' || owner, ',' ORDER BY name) FROM (" +
      "SELECT c.relname::text AS name, pg_get_userbyid(c.relowner) AS owner FROM pg_class c " +
      "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'app' " +
      "UNION ALL SELECT p.oid::regprocedure::text, pg_get_userbyid(p.proowner) FROM pg_proc p " +
      "JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'app' " +
      "UNION ALL SELECT 'schema app', pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = 'app' " +
      "UNION ALL SELECT 'public.app_schema_migrations', pg_get_userbyid(relowner) FROM pg_class " +
      "WHERE oid = 'public.app_schema_migrations'::regclass) x",
  );
  result._policies = await psql(
    exec,
    "SELECT md5(coalesce(string_agg(concat_ws(':', tablename, policyname, permissive, roles::text, cmd, qual, with_check), '|' " +
      "ORDER BY tablename, policyname), '')) FROM pg_policies WHERE schemaname = 'app'",
  );
  result._roles = await psql(
    exec,
    "SELECT string_agg(concat_ws(':', r.rolname, r.rolcanlogin, r.rolinherit, r.rolbypassrls, r.rolsuper, " +
      "(SELECT string_agg(m.rolname, '+' ORDER BY m.rolname) FROM pg_auth_members a JOIN pg_roles m ON m.oid = a.member " +
      "WHERE a.roleid = r.oid)), ',' ORDER BY r.rolname) FROM pg_roles r WHERE r.rolname IN ('app_api', 'app_worker')",
  );
  result._migrations = await psql(
    exec,
    "SELECT string_agg(version || ':' || checksum, ',' ORDER BY version) FROM public.app_schema_migrations",
  );
  return result;
}

const changed = (a, b) => Object.keys({ ...a, ...b }).filter((key) => JSON.stringify(a[key]) !== JSON.stringify(b[key]));

const timer = () => {
  const start = process.hrtime.bigint();
  return () => Number((process.hrtime.bigint() - start) / 1_000_000n);
};

async function waitUntilReady() {
  const deadline = Date.now() + READY_TIMEOUT_MS;
  // TCP, not the socket: the image's first-start initialization runs a
  // temporary socket-only server that would answer too early.
  for (;;) {
    try {
      await target(["pg_isready", "-h", "127.0.0.1", "-U", "postgres", "-d", DB]);
      return;
    } catch (error) {
      if (abort.signal.aborted || Date.now() > deadline) throw error;
      await sleep(250, undefined, { signal: abort.signal });
    }
  }
}

const report = { source: `${DB} (docker compose postgres)`, target: null, steps: {} };
let started = false;
try {
  await source(["pg_isready", "-U", "postgres"]);
  const container = (await run("docker", ["compose", "ps", "--quiet", "postgres"])).trim();
  const image = (await run("docker", ["inspect", "--format", "{{.Image}}", container])).trim();
  const tag = (await run("docker", ["inspect", "--format", "{{.Config.Image}}", container])).trim();
  report.target = `${DB} in a fresh ${tag} container, no network`;

  const before = await fingerprint(source);
  let elapsed = timer();
  await source(["pg_dump", "-U", "postgres", "-d", DB, "--format=custom", "--file", DUMP], { uninterruptible: true });
  report.steps.dump_ms = elapsed();
  report.dump_bytes = Number((await source(["stat", "-c", "%s", DUMP])).trim());
  // A second fingerprint brackets the dump: if the source changed meanwhile
  // (for example, tests running against it), the comparison means nothing.
  const sourceChanged = changed(before, await fingerprint(source));
  if (sourceChanged.length) {
    throw new Error(`the source database changed during the dump (${sourceChanged.join(", ")}); rerun when it is idle`);
  }

  elapsed = timer();
  // Set before `docker run`, so cleanup removes the container even if this
  // step fails after creating it (docker rm --force accepts a missing one).
  started = true;
  // The image requires a password. This one is random, unused (the drill
  // connects through the container's local socket; the container has no
  // network), and passed through the environment so that it is never on a
  // command line or in an error message.
  await run(
    "docker",
    ["run", "--detach", "--rm", "--pull", "never", "--network", "none", "--name", TARGET,
      "--env", "POSTGRES_PASSWORD", "--env", `POSTGRES_DB=${DB}`, image],
    { env: { ...process.env, POSTGRES_PASSWORD: randomBytes(24).toString("hex") }, uninterruptible: true },
  );
  await waitUntilReady();
  report.steps.fresh_server_ms = elapsed();

  // Held in memory between the containers; fine for local development data.
  await target(["sh", "-c", `cat > ${DUMP}`], { input: await source(["cat", DUMP], { binary: true }) });

  elapsed = timer();
  await psql(target, ROLES_SQL);
  // Owners are restored too (no --no-owner), which needs the roles above.
  await target(["pg_restore", "-U", "postgres", "-d", DB, "--exit-on-error", DUMP]);
  report.steps.restore_ms = elapsed();

  elapsed = timer();
  const after = await fingerprint(target);
  report.steps.verify_ms = elapsed();

  const mismatches = changed(before, after);
  report.tables = Object.fromEntries(Object.entries(before).filter(([k]) => !k.startsWith("_")).map(([k, v]) => [k, v.rows]));
  report.rls_flags_match = before._rls === after._rls;
  report.mismatches = mismatches;
  report.result = mismatches.length === 0 ? "PASS" : "FAIL";
} catch (error) {
  report.result = "FAIL";
  report.error = interrupted
    ? `interrupted by ${interrupted}`
    : String(error.stderr || error.message).trim().split("\n").slice(-5).join("\n");
} finally {
  if (started) {
    try {
      await run("docker", ["rm", "--force", "--volumes", TARGET], { uninterruptible: true });
    } catch (error) {
      if (!/no such container/i.test(error.stderr ?? "")) {
        console.error(`Could not remove the drill container; remove it with: docker rm -f ${TARGET}`);
      }
    }
  }
  await source(["rm", "-f", DUMP], { uninterruptible: true }).catch(() => {
    console.error(`Could not remove ${DUMP} from the postgres service container.`);
  });
}

console.log(JSON.stringify(report, null, 2));
process.exit(interrupted ? 128 + constants.signals[interrupted] : report.result === "PASS" ? 0 : 1);
