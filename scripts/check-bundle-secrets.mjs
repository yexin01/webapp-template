#!/usr/bin/env node
// ENV-07: the browser bundle must not contain the values of server-only
// variables or secret-shaped strings. Run after `pnpm build`.
//
// Variable *names* may legitimately appear inside SDK code (for example,
// Clerk's shared code reads process.env.CLERK_SECRET_KEY, which is undefined
// in browsers), so the check compares actual values. Values are read from the
// environment and the root .env file and are never printed.
//
//   node scripts/check-bundle-secrets.mjs              scan apps/web/.next/static
//   node scripts/check-bundle-secrets.mjs --self-test  prove planted secrets are caught
import { randomBytes } from "node:crypto";
import { mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");

const SERVER_ONLY_NAMES = [
  "CLERK_SECRET_KEY",
  "CLERK_WEBHOOK_SIGNING_SECRET",
  "CLERK_JWT_KEY",
  "STRIPE_SECRET_KEY",
  "STRIPE_WEBHOOK_SECRET",
  "DATABASE_URL",
  "LLM_API_KEY",
  "SENTRY_AUTH_TOKEN",
  "SENTRY_DSN",
  "CRON_SECRET",
];
const SECRET_PATTERNS = [
  /\b[sr]k_(?:live|test)_[A-Za-z0-9]{10,}/, // Stripe and Clerk secret / restricted keys
  /\bwhsec_[A-Za-z0-9+/=]{10,}/, // webhook signing secrets
  /\bsk-ant-[A-Za-z0-9_-]{20,}/, // Anthropic API keys
  /\bsk-proj-[A-Za-z0-9_-]{20,}/, // OpenAI project keys
  /\bsntrys_[A-Za-z0-9+/=_-]{20,}/, // Sentry auth tokens
  /postgres(?:ql)?:\/\/[^\s"']+:[^\s"'@]+@/, // database URLs with a password
  /-----BEGIN [A-Z ]*PRIVATE KEY-----/,
];

function* files(dir) {
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry);
    if (statSync(full).isDirectory()) yield* files(full);
    else if (/\.(js|css|json|html|map)$/.test(entry)) yield full;
  }
}

/**
 * Parses .env text into the candidate values of each variable: `export KEY=`
 * lines, quoted values (a `#` inside quotes is kept), unquoted values with a
 * trailing comment, and surrounding whitespace. An unquoted value containing
 * `#` is read differently by dotenv and Node (cut at the first `#`) and by
 * python-dotenv (cut at ` #`), so both readings are candidates.
 */
export function parseEnvFile(text) {
  const values = new Map();
  for (const line of text.split(/\r?\n/)) {
    const match = /^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/.exec(line);
    if (!match) continue;
    const raw = match[2].trim();
    const quote = raw[0];
    let candidates;
    if (quote === '"' || quote === "'" || quote === "`") {
      const end = raw.indexOf(quote, 1);
      candidates = [end === -1 ? raw.slice(1) : raw.slice(1, end)];
    } else {
      candidates = [raw.replace(/#.*$/, "").trim(), raw.replace(/(^|\s)#.*$/, "").trim()];
    }
    values.set(match[1], [...new Set(candidates)]);
  }
  return values;
}

function serverOnlyValues(envText, environment) {
  const values = [];
  for (const [name, candidates] of parseEnvFile(envText)) {
    if (SERVER_ONLY_NAMES.includes(name)) for (const value of candidates) values.push([name, value]);
  }
  for (const name of SERVER_ONLY_NAMES) if (environment[name]) values.push([name, environment[name].trim()]);
  // Ignore empty and very short values to avoid meaningless matches.
  return values.filter(([, value]) => value.length >= 12);
}

/** @returns findings, each naming a file and a variable or pattern, never a value */
export function scan(staticDir, secrets) {
  const findings = [];
  for (const file of files(staticDir)) {
    const text = readFileSync(file, "utf8");
    const where = path.relative(root, file);
    for (const [name, value] of secrets) if (text.includes(value)) findings.push(`${where}: value of ${name}`);
    for (const pattern of SECRET_PATTERNS) if (pattern.test(text)) findings.push(`${where}: matches ${pattern}`);
  }
  return findings;
}

function readEnvFile() {
  try {
    return readFileSync(path.join(root, ".env"), "utf8");
  } catch {
    return "";
  }
}

/**
 * Plants secrets in a fake build and asserts each one is found, and that a
 * clean build passes. The planted values are generated, so no secret-shaped
 * literal is committed.
 */
function selfTest() {
  const dir = mkdtempSync(path.join(tmpdir(), "bundle-secrets-"));
  const failures = [];
  try {
    const staticDir = path.join(dir, ".next", "static");
    mkdirSync(path.join(staticDir, "chunks"), { recursive: true });
    const token = (length) => randomBytes(length).toString("hex").slice(0, length);
    const cronSecret = token(40);
    const llmKey = `${token(16)} # ${token(16)}`;
    const databaseUrl = token(24);
    const webhookSecret = `${token(16)}#${token(16)}`;
    const envText = [
      `export CRON_SECRET=${cronSecret}   # set by the operator`,
      `LLM_API_KEY="${llmKey}"`,
      `DATABASE_URL=${databaseUrl}   `,
      `CLERK_WEBHOOK_SIGNING_SECRET=${webhookSecret}`,
    ].join("\n");
    const planted = {
      "value of CRON_SECRET": cronSecret,
      "value of LLM_API_KEY": llmKey,
      "value of DATABASE_URL": databaseUrl,
      // Unquoted with "#": python-dotenv keeps all of it, dotenv and Node only the part before "#".
      "value of CLERK_WEBHOOK_SIGNING_SECRET": webhookSecret,
      "value of CLERK_WEBHOOK_SIGNING_SECRET ": webhookSecret.split("#")[0],
      "value of STRIPE_SECRET_KEY": `stripe-${token(30)}`,
      "sk-ant-": `sk-${"ant"}-api03-${token(40)}`,
      "sk-proj-": `sk-${"proj"}-${token(40)}`,
      sntrys_: `${"sntrys"}_${token(40)}`,
      "[sr]k_": `${"sk"}_${"live"}_${token(24)}`,
      whsec_: `${"whsec"}_${token(24)}`,
      "postgres": `postgresql://app:${token(16)}@db.example.internal/app`,
    };
    const secrets = serverOnlyValues(envText, { STRIPE_SECRET_KEY: planted["value of STRIPE_SECRET_KEY"] });

    writeFileSync(path.join(staticDir, "chunks", "clean.js"), 'console.log("process.env.CLERK_SECRET_KEY");\n');
    const clean = scan(staticDir, secrets);
    if (clean.length) failures.push(`a clean build was flagged: ${clean.join("; ")}`);

    for (const [label, value] of Object.entries(planted)) {
      const file = path.join(staticDir, "chunks", "planted.js");
      writeFileSync(file, `const leaked = ${JSON.stringify(value)};\n`);
      const found = scan(staticDir, secrets);
      if (!found.some((finding) => finding.includes(label.trim()))) failures.push(`missed ${label.trim()}`);
      if (found.some((finding) => finding.includes(value))) failures.push(`printed the value of ${label}`);
      rmSync(file);
    }
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
  if (failures.length) {
    console.error(`Bundle secret scan self-test FAILED:\n  - ${failures.join("\n  - ")}`);
    process.exit(1);
  }
  console.log("Bundle secret scan self-test passed: planted values and secret patterns are caught, values never printed.");
}

if (process.argv.includes("--self-test")) {
  selfTest();
  process.exit(0);
}

const staticDir = path.join(root, "apps/web/.next/static");
let exists = true;
try {
  statSync(staticDir);
} catch {
  exists = false;
}
if (!exists) {
  console.error("No build output found. Run `pnpm build` first.");
  process.exit(2);
}

const secrets = serverOnlyValues(readEnvFile(), process.env);
const findings = scan(staticDir, secrets);
if (findings.length) {
  console.error("Server-only values found in the browser bundle (values not shown):");
  for (const f of findings) console.error(`  - ${f}`);
  process.exit(1);
}
console.log(`No server-only values (${secrets.length} checked) or secret-shaped strings found in the browser bundle.`);
