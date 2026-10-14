#!/usr/bin/env node
/**
 * Local backup-and-restore drill (supports DEC-005; does NOT resolve it).
 *
 * Dumps the local Docker database, restores it into a scratch database on the
 * same local server, and verifies per-table row counts, content checksums,
 * and row-level-security flags, then drops the scratch database. Prints
 * timings as JSON. Touches only the local docker-compose PostgreSQL service.
 *
 *   pnpm db:restore-drill
 *
 * This proves the dump format and the restore procedure for this schema. It
 * is not evidence about a hosted provider's backups, recovery objectives, or
 * point-in-time recovery (spec §8.4).
 */
import { execFileSync } from "node:child_process";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const SOURCE = "app";
const SCRATCH = "app_restore_drill";
const DUMP_IN_CONTAINER = "/tmp/restore-drill.dump";

function compose(args, options = {}) {
  return execFileSync("docker", ["compose", "exec", "-T", "postgres", ...args], {
    cwd: root,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
    ...options,
  });
}

const psql = (db, sql) => compose(["psql", "-U", "postgres", "-d", db, "-v", "ON_ERROR_STOP=1", "-At", "-c", sql]).trim();

function fingerprint(db) {
  const tables = psql(db, "SELECT tablename FROM pg_tables WHERE schemaname = 'app' ORDER BY 1").split("\n").filter(Boolean);
  const result = {};
  for (const table of tables) {
    // Table names come from the catalog of a local database, not from input.
    const [count, digest] = psql(
      db,
      `SELECT count(*), coalesce(md5(string_agg(t::text, '|' ORDER BY t::text)), '-') FROM app."${table}" t`,
    ).split("|");
    result[table] = { rows: Number(count), md5: digest };
  }
  result._rls = psql(
    db,
    "SELECT string_agg(c.relname || ':' || c.relrowsecurity || ':' || c.relforcerowsecurity, ',' ORDER BY c.relname) " +
      "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'app' AND c.relkind = 'r'",
  );
  result._migrations = psql(
    db,
    "SELECT string_agg(version || ':' || checksum, ',' ORDER BY version) FROM public.app_schema_migrations",
  );
  return result;
}

const timer = () => {
  const start = process.hrtime.bigint();
  return () => Number((process.hrtime.bigint() - start) / 1_000_000n);
};

const report = { source: SOURCE, scratch: SCRATCH, steps: {} };
try {
  compose(["pg_isready", "-U", "postgres"]);
  const before = fingerprint(SOURCE);

  let elapsed = timer();
  compose(["pg_dump", "-U", "postgres", "-d", SOURCE, "--format=custom", "--file", DUMP_IN_CONTAINER]);
  report.steps.dump_ms = elapsed();
  report.dump_bytes = Number(compose(["stat", "-c", "%s", DUMP_IN_CONTAINER]).trim());

  compose(["dropdb", "-U", "postgres", "--if-exists", SCRATCH]);
  compose(["createdb", "-U", "postgres", SCRATCH]);
  elapsed = timer();
  compose(["pg_restore", "-U", "postgres", "-d", SCRATCH, "--exit-on-error", "--no-owner", DUMP_IN_CONTAINER]);
  report.steps.restore_ms = elapsed();

  elapsed = timer();
  const after = fingerprint(SCRATCH);
  report.steps.verify_ms = elapsed();

  const mismatches = Object.keys({ ...before, ...after }).filter(
    (key) => JSON.stringify(before[key]) !== JSON.stringify(after[key]),
  );
  report.tables = Object.fromEntries(Object.entries(before).filter(([k]) => !k.startsWith("_")).map(([k, v]) => [k, v.rows]));
  report.rls_flags_match = before._rls === after._rls;
  report.mismatches = mismatches;
  report.result = mismatches.length === 0 ? "PASS" : "FAIL";
} finally {
  try {
    compose(["dropdb", "-U", "postgres", "--if-exists", SCRATCH]);
    compose(["rm", "-f", DUMP_IN_CONTAINER]);
  } catch {
    /* best effort cleanup */
  }
}

console.log(JSON.stringify(report, null, 2));
process.exit(report.result === "PASS" ? 0 : 1);
