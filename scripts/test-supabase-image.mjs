#!/usr/bin/env node
/**
 * Supabase parity check (local provider-image evidence; not a hosted project).
 *
 * The default test database is plain PostgreSQL where `postgres` is a
 * superuser. On Supabase's image it is not (NOSUPERUSER, CREATEROLE, BYPASSRLS),
 * `supautils` guards role management, and the client roles anon, authenticated,
 * and service_role exist with default grants in `public`. This script starts
 * that image in a throwaway container and:
 *
 * 1. applies the migrations twice as `postgres`, the way a hosted project
 *    does (the second run applies nothing);
 * 2. checks what the migrations must leave behind: the migration ledger and
 *    schema `app` are out of reach of the three client roles, the runtime
 *    roles have the attributes DEC-008 requires, and `lock_my_membership` is
 *    owned by app_worker;
 * 3. runs `api-login`, then shows that app_login can switch into the runtime
 *    roles but, outside them, reads nothing in schema `app`, that the API's
 *    startup check accepts it, and that it refuses the schema owner `postgres`;
 * 4. runs the API's database tests against the image.
 *
 *   pnpm test:supabase                    # all database tests
 *   pnpm test:supabase tests/integration/test_rls.py   # only these
 *   SUPABASE_POSTGRES_IMAGE=<image:tag> pnpm test:supabase
 *   SUPABASE_TEST_CONTAINER=<name> SUPABASE_TEST_PORT=<port> pnpm test:supabase
 *
 * The container is named after this process (or SUPABASE_TEST_CONTAINER),
 * listens on a free port (or SUPABASE_TEST_PORT) of 127.0.0.1 only, uses a
 * random password, and is removed on PASS, on FAIL, and after SIGINT, SIGTERM,
 * or SIGHUP. Needs Docker, uv, and Node (some tests
 * run Node scripts); run `pnpm api:sync` first. Exit status: 0 on PASS, 1 on
 * FAIL, 2 when Docker is unavailable.
 *
 * The image tag is the one the Supabase CLI runs locally, which still has the
 * supautils that crashes on `GRANT ... TO CURRENT_USER`: that is what keeps
 * the grants in the migrations named. Dependabot does not read this constant;
 * try a newer tag with SUPABASE_POSTGRES_IMAGE before moving it.
 */
import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { createServer } from "node:net";
import path from "node:path";
import { setTimeout as sleep } from "node:timers/promises";

const IMAGE = process.env.SUPABASE_POSTGRES_IMAGE || "public.ecr.aws/supabase/postgres:17.6.1.106";
const root = path.resolve(import.meta.dirname, "..");
const api = path.join(root, "apps/api");
const NAME = process.env.SUPABASE_TEST_CONTAINER || `supabase-image-test-${process.pid}-${randomBytes(3).toString("hex")}`;
const READY_TIMEOUT_MS = 180_000;
const password = randomBytes(18).toString("hex");
const loginPassword = randomBytes(18).toString("hex");
const pytestArgs = process.argv.slice(2);

const abort = new AbortController();
let interrupted = null;
for (const signal of ["SIGINT", "SIGTERM", "SIGHUP"]) {
  process.on(signal, () => {
    interrupted ??= signal;
    abort.abort();
  });
}

/** Runs a command to its end; resolves with its output. Output is echoed when `echo` is set. */
function run(command, args, { env = {}, cwd = root, echo = false, uninterruptible = false } = {}) {
  return new Promise((resolve, reject) => {
    if (!uninterruptible && abort.signal.aborted) return reject(new Error(`interrupted by ${interrupted}`));
    const child = spawn(command, args, {
      cwd,
      env: { ...process.env, ...env },
      stdio: ["ignore", "pipe", "pipe"],
      detached: uninterruptible,
      signal: uninterruptible ? undefined : abort.signal,
    });
    let output = "";
    const take = (stream, sink) =>
      stream.on("data", (chunk) => {
        output += chunk;
        if (echo) sink.write(chunk);
      });
    take(child.stdout, process.stdout);
    take(child.stderr, process.stderr);
    child.on("error", reject);
    child.on("close", (code, signal) => {
      if (code === 0) return resolve(output);
      const error = new Error(`${command} ${args.slice(0, 3).join(" ")} failed (${signal ?? `exit ${code}`})`);
      error.output = output;
      reject(error);
    });
  });
}

const freePort = () =>
  new Promise((resolve, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });

/** psql inside the container, over TCP as `user`, one statement list; returns its unaligned output. */
const psql = async (user, pass, sql, database = "postgres") =>
  (
    await run("docker", [
      "exec", "--env", "PGPASSWORD", NAME,
      "psql", "-h", "127.0.0.1", "-U", user, "-d", database, "-v", "ON_ERROR_STOP=1", "-qAt", "-c", sql,
    ].flat(), { env: { PGPASSWORD: pass } }) // fmt: skip
  ).trim();

async function waitUntilReady() {
  const deadline = Date.now() + READY_TIMEOUT_MS;
  // The image's first start runs a temporary server and creates the client roles; wait for both.
  for (;;) {
    try {
      const roles = await psql("postgres", password, "SELECT count(*) FROM pg_roles WHERE rolname = 'service_role'");
      if (roles === "1") return;
    } catch (error) {
      if (abort.signal.aborted) throw error;
    }
    if (Date.now() > deadline) throw new Error("the database did not become ready in time");
    await sleep(500, undefined, { signal: abort.signal });
  }
}

const failures = [];
const expect = (label, actual, expected) => {
  const ok = actual === expected;
  console.log(`${ok ? "ok  " : "FAIL"} ${label}`);
  if (!ok) failures.push(`${label}\n  expected: ${JSON.stringify(expected)}\n  actual:   ${JSON.stringify(actual)}`);
};

let started = false;
let pytestSummary = "";
try {
  try {
    await run("docker", ["info"]);
  } catch {
    console.error("Docker is not available.");
    process.exit(2);
  }
  const port = Number(process.env.SUPABASE_TEST_PORT) || (await freePort());
  console.log(`Starting ${IMAGE} as ${NAME} on 127.0.0.1:${port}`);
  started = true; // set first, so cleanup removes the container even if this step fails after creating it
  await run(
    "docker",
    ["run", "--detach", "--pull", "missing", "--name", NAME, "--env", "POSTGRES_PASSWORD", "--publish", `127.0.0.1:${port}:5432`, IMAGE],
    { env: { POSTGRES_PASSWORD: password }, uninterruptible: true },
  );
  await waitUntilReady();

  const owner = await psql("postgres", password, "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'postgres'");
  expect("postgres is not a superuser here, but bypasses RLS", owner, "f|t");
  console.log(`session_preload_libraries: ${await psql("postgres", password, "SHOW session_preload_libraries")}`);

  const url = `postgresql://postgres:${password}@127.0.0.1:${port}/postgres`;
  const cli = (args, env = {}) =>
    run("uv", ["run", "--frozen", "python", "-m", "app.cli", ...args], {
      cwd: api,
      env: { APP_ENV: "local", DATABASE_URL: url, MIGRATION_DATABASE_URL: "", ...env },
    });

  console.log("\n== Migrations, twice, as postgres");
  const first = JSON.parse((await cli(["migrate"])).trim().split("\n").at(-1)).applied.length;
  expect("the first run applies every migration", first > 0, true);
  expect("the second run applies none", (await cli(["migrate"])).trim().split("\n").at(-1), '{"applied": []}');

  console.log("\n== What the migrations leave behind");
  const reachable = await psql(
    "postgres", password,
    `SELECT string_agg(r.rolname, ',' ORDER BY r.rolname)
     FROM (VALUES ('anon'), ('authenticated'), ('service_role')) r(rolname)
     WHERE has_table_privilege(r.rolname, 'public.app_schema_migrations', 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
        OR has_schema_privilege(r.rolname, 'app', 'USAGE')
        OR EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                   WHERE n.nspname = 'app' AND c.relkind IN ('r', 'p', 'v', 'm')
                     AND has_table_privilege(r.rolname, c.oid, 'SELECT,INSERT,UPDATE,DELETE'))`,
  ); // fmt: skip
  expect("anon, authenticated, and service_role reach neither the ledger nor schema app", reachable, "");
  expect(
    "the runtime and login roles have no unsafe attribute",
    await psql(
      "postgres", password,
      `SELECT string_agg(concat_ws(':', rolname, rolcanlogin, rolinherit, rolsuper, rolbypassrls, rolcreaterole), ',' ORDER BY rolname)
       FROM pg_roles WHERE rolname IN ('app_api', 'app_worker', 'app_login')`,
    ),
    "app_api:f:f:f:f:f,app_login:f:f:f:f:f,app_worker:f:f:f:f:f",
  ); // fmt: skip
  expect(
    "app_login is a member of app_api and app_worker only",
    await psql(
      "postgres", password,
      "SELECT string_agg(g.rolname, ',' ORDER BY g.rolname) FROM pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid WHERE m.member = 'app_login'::regrole",
    ),
    "app_api,app_worker",
  ); // fmt: skip
  expect(
    "lock_my_membership is owned by app_worker",
    await psql("postgres", password, "SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE oid = 'app.lock_my_membership(uuid)'::regprocedure"),
    "app_worker",
  ); // fmt: skip

  console.log("\n== The runtime login");
  await cli(["api-login"], { API_DB_PASSWORD: loginPassword });
  const asLogin = (sql) => psql("app_login", loginPassword, sql);
  expect(
    "app_login can switch into app_api and read as a tenant",
    await asLogin("BEGIN; SELECT set_config('role', 'app_api', true); SELECT count(*) FROM app.projects; ROLLBACK;"),
    "app_api\n0",
  );
  let leftOut = "";
  try {
    await asLogin("BEGIN; SELECT set_config('role', 'none', true); SELECT count(*) FROM app.projects; ROLLBACK;");
  } catch (error) {
    leftOut = /permission denied for schema app/.test(error.output ?? "") ? "permission denied" : error.output;
  }
  expect("outside the runtime roles, app_login reads nothing in schema app", leftOut, "permission denied");
  const guard = `
import os
from app.kernel.infrastructure.db import Database, UnsafeLoginRoleError
db = Database(os.environ["LOGIN_URL"], api_pool_size=1, worker_pool_size=2, connect_timeout=5, statement_timeout_ms=5000, enforce_login_role=True)
try:
    with db.transaction("app_api", {}) as conn:
        print("allowed", conn.execute("SELECT current_user::text").fetchone()[0])
except UnsafeLoginRoleError:
    print("refused")
finally:
    db.close()
`;
  const startupCheck = async (loginUrl) =>
    (await run("uv", ["run", "--frozen", "python", "-c", guard], { cwd: api, env: { LOGIN_URL: loginUrl } }))
      .trim()
      .split("\n")
      .at(-1);
  expect(
    "the API's startup check accepts app_login",
    await startupCheck(`postgresql://app_login:${loginPassword}@127.0.0.1:${port}/postgres`),
    "allowed app_api",
  );
  expect("the API's startup check refuses the schema owner", await startupCheck(url), "refused");

  console.log("\n== API database tests on the image");
  const selection = pytestArgs.length ? pytestArgs : ["-m", "db"];
  const output = await run("uv", ["run", "--frozen", "pytest", "-q", "-p", "no:cacheprovider", ...selection], {
    cwd: api,
    env: { TEST_DATABASE_URL: url },
    echo: true,
  }).catch((error) => {
    failures.push("the API database tests failed");
    return error.output ?? "";
  });
  pytestSummary = output.trim().split("\n").filter(Boolean).at(-1) ?? "";
} catch (error) {
  failures.push(interrupted ? `interrupted by ${interrupted}` : String(error.output || error.message).slice(-1500));
} finally {
  if (started) {
    try {
      await run("docker", ["rm", "--force", "--volumes", NAME], { uninterruptible: true });
    } catch (error) {
      if (!/no such container/i.test(error.output ?? "")) {
        console.error(`Could not remove the container; remove it with: docker rm -f ${NAME}`);
      }
    }
  }
}

if (failures.length) {
  console.error(`\nSupabase image check FAILED:\n- ${failures.join("\n- ")}`);
  process.exit(1);
}
console.log(`\nSupabase image check passed on ${IMAGE}. Tests: ${pytestSummary}`);
