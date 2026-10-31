#!/usr/bin/env node
// ENV-07: the browser bundle must not contain the values of server-only
// variables or secret-shaped strings. Run after `pnpm build`.
//
// Variable *names* may legitimately appear inside SDK code (for example,
// Clerk's shared code reads process.env.CLERK_SECRET_KEY, which is undefined
// in browsers), so the check compares actual values. Values are read from the
// environment and the root .env file and are never printed.
//
// What browsers receive: everything in .next/static, and the prerendered
// pages Next serves as they are (HTML, RSC payloads, route-handler bodies,
// their headers, and Pages Router data) under .next/server/app and
// .next/server/pages. Server JavaScript and build manifests there never reach
// a browser and are not scanned.
//
//   node scripts/check-bundle-secrets.mjs                   scan apps/web/.next
//   node scripts/check-bundle-secrets.mjs --require-values  also fail when no value was available to compare
//   node scripts/check-bundle-secrets.mjs --self-test       prove planted secrets are caught
//
// On Vercel (Services), every project variable reaches the web service's build
// and runtime, API secrets included, so the values to compare exist there even
// though the web app never reads them.
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
  "MIGRATION_DATABASE_URL",
  "API_DB_PASSWORD",
  "LLM_API_KEY",
  "SENTRY_AUTH_TOKEN",
  "CRON_SECRET",
];
// SENTRY_DSN is deliberately absent: a DSN is a public client key, and the
// browser receives the same value as NEXT_PUBLIC_SENTRY_DSN.
const SECRET_PATTERNS = [
  /\b[sr]k_(?:live|test)_[A-Za-z0-9]{10,}/, // Stripe and Clerk secret / restricted keys
  /\bwhsec_[A-Za-z0-9+/=]{10,}/, // webhook signing secrets
  /\bsk-ant-[A-Za-z0-9_-]{20,}/, // Anthropic API keys
  /\bsk-proj-[A-Za-z0-9_-]{20,}/, // OpenAI project keys
  /\bsntrys_[A-Za-z0-9+/=_-]{20,}/, // Sentry auth tokens
  /\bph[xsar]_[A-Za-z0-9]{20,}/, // PostHog personal, secret and OAuth keys (the public project token is phc_)
  /postgres(?:ql)?:\/\/[^\s"']+:[^\s"'@]+@/, // database URLs with a password
  /-----BEGIN [A-Z ]*PRIVATE KEY-----/,
];

const isDirectory = (dir) => {
  try {
    return statSync(dir).isDirectory();
  } catch {
    return false;
  }
};

// Next's own build manifests (and *.nft.json file traces) beside the server
// code. Only these names are skipped: a route of the same shape, such as
// pages/manifest.json, is still scanned.
const NEXT_SERVER_FILES = new Set([
  "app-paths-manifest.json",
  "build-manifest.json",
  "dynamic-css-manifest.json",
  "font-manifest.json",
  "functions-config-manifest.json",
  "middleware-manifest.json",
  "next-font-manifest.json",
  "pages-manifest.json",
  "prefetch-hints.json",
  "react-loadable-manifest.json",
  "server-reference-manifest.json",
  "subresource-integrity-manifest.json",
]);

/** Browser-facing outputs of a Next build directory, each with the files to scan. */
const TARGETS = [
  { dir: "static", include: (entry) => /\.(js|mjs|css|json|html|map|rsc|txt)$/.test(entry) },
  ...["server/app", "server/pages"].map((dir) => ({
    dir,
    // Prerendered HTML, RSC payloads (including .segments/), route-handler
    // bodies and headers, and Pages Router data; not server code or manifests.
    include: (entry) =>
      /\.(html|rsc|body|meta)$/.test(entry) ||
      (entry.endsWith(".json") && !entry.endsWith(".nft.json") && !NEXT_SERVER_FILES.has(entry)),
  })),
];

function* files(dir, include) {
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry);
    if (statSync(full).isDirectory()) yield* files(full, include);
    else if (include(entry)) yield full;
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

const URL_NAMES = ["DATABASE_URL", "MIGRATION_DATABASE_URL"];

/** The password of a connection URL, as written and decoded: a bundle can hold it without the rest of the URL. */
function urlPasswords(value) {
  try {
    const { password } = new URL(value);
    return password ? [...new Set([password, decodeURIComponent(password)])] : [];
  } catch {
    return [];
  }
}

function serverOnlyValues(envText, environment) {
  const values = [];
  for (const [name, candidates] of parseEnvFile(envText)) {
    if (SERVER_ONLY_NAMES.includes(name)) for (const value of candidates) values.push([name, value]);
  }
  for (const name of SERVER_ONLY_NAMES) if (environment[name]) values.push([name, environment[name].trim()]);
  for (const [name, value] of [...values]) {
    if (URL_NAMES.includes(name)) for (const password of urlPasswords(value)) values.push([name, password]);
    for (const line of pemLines(value)) values.push([name, line]);
  }
  // Ignore empty and very short values to avoid meaningless matches.
  return values.filter(([, value]) => value.length >= 12);
}

/**
 * The base64 lines of a PEM value (CLERK_JWT_KEY). A bundle holds a multi-line value as one string with its
 * line breaks escaped, so the whole value never matches; each line does. A one-line value writes the breaks as `\n`.
 */
function pemLines(value) {
  if (!value.includes("-----BEGIN ")) return [];
  return value
    .split(/\r?\n|\\n/)
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith("-----"));
}

/**
 * A scan that compared no value checked only the secret-shaped patterns. With
 * --require-values that is a failure: the build environment held no secrets to compare.
 * @returns a message, or null when the scan may proceed
 */
export function comparisonProblem(secrets, required) {
  if (!required || secrets.length) return null;
  return "No server-only value was available to compare: set them in the build environment, or drop --require-values.";
}

/**
 * Scans the browser-facing outputs of a Next build directory (`.next`).
 * @returns findings, each naming a file and a variable or pattern, never a value
 */
export function scan(nextDir, secrets) {
  const findings = new Set(); // a value compared in several parts (a PEM's lines) is one finding per file
  for (const { dir, include } of TARGETS) {
    if (!isDirectory(path.join(nextDir, dir))) continue;
    for (const file of files(path.join(nextDir, dir), include)) {
      const text = readFileSync(file, "utf8");
      const where = path.relative(root, file);
      for (const [name, value] of secrets) if (text.includes(value)) findings.add(`${where}: value of ${name}`);
      for (const pattern of SECRET_PATTERNS) if (pattern.test(text)) findings.add(`${where}: matches ${pattern}`);
    }
  }
  return [...findings];
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
    const nextDir = path.join(dir, ".next");
    const staticDir = path.join(nextDir, "static");
    const appDir = path.join(nextDir, "server", "app");
    const pagesDir = path.join(nextDir, "server", "pages");
    mkdirSync(path.join(staticDir, "chunks"), { recursive: true });
    mkdirSync(path.join(appDir, "pricing"), { recursive: true });
    mkdirSync(pagesDir, { recursive: true });
    const token = (length) => randomBytes(length).toString("hex").slice(0, length);
    const cronSecret = token(40);
    const llmKey = `${token(16)} # ${token(16)}`;
    const databaseUrl = token(24);
    const webhookSecret = `${token(16)}#${token(16)}`;
    const migrationPassword = token(24);
    const encodedPassword = `${token(10)}@${token(10)}/${token(4)}`;
    const publicKey = ["-----BEGIN PUBLIC KEY-----", token(64), token(64), token(40), "-----END PUBLIC KEY-----"];
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
      // Only the password of a connection URL reaches the bundle, as written or decoded.
      "value of MIGRATION_DATABASE_URL": migrationPassword,
      "value of DATABASE_URL ": encodedPassword,
      // A bundle holds a PEM on one line with escaped breaks (or on several, in prerendered output).
      "value of CLERK_JWT_KEY": publicKey.join("\n"),
      "sk-ant-": `sk-${"ant"}-api03-${token(40)}`,
      "sk-proj-": `sk-${"proj"}-${token(40)}`,
      sntrys_: `${"sntrys"}_${token(40)}`,
      "ph[xsar]_": `${"phx"}_${token(40)}`,
      "[sr]k_": `${"sk"}_${"live"}_${token(24)}`,
      whsec_: `${"whsec"}_${token(24)}`,
      "postgres": `postgresql://app:${token(16)}@db.example.internal/app`,
    };
    const secrets = serverOnlyValues(envText, {
      STRIPE_SECRET_KEY: planted["value of STRIPE_SECRET_KEY"],
      MIGRATION_DATABASE_URL: `postgresql://postgres.ref:${migrationPassword}@pooler.example.internal:5432/postgres`,
      DATABASE_URL: `postgresql://app_login.ref:${encodeURIComponent(encodedPassword)}@pooler.example.internal/postgres`,
      CLERK_JWT_KEY: publicKey.join("\n"),
    });

    writeFileSync(path.join(staticDir, "chunks", "clean.js"), 'console.log("process.env.CLERK_SECRET_KEY");\n');
    writeFileSync(path.join(appDir, "pricing.html"), "<p>Pricing</p>\n");
    writeFileSync(path.join(appDir, "pricing.rsc"), '1:["$","p",null,{"children":"Pricing"}]\n');
    // Server code and build manifests never reach a browser: values there are not findings.
    writeFileSync(path.join(appDir, "pricing", "page.js"), `const key = ${JSON.stringify(cronSecret)};\n`);
    writeFileSync(path.join(appDir, "pricing", "build-manifest.json"), JSON.stringify({ key: cronSecret }));
    writeFileSync(path.join(appDir, "pricing", "page.js.nft.json"), JSON.stringify({ key: cronSecret }));
    writeFileSync(path.join(pagesDir, "_app.js"), `const key = ${JSON.stringify(cronSecret)};\n`);
    const clean = scan(nextDir, secrets);
    if (clean.length) failures.push(`a clean build was flagged: ${clean.join("; ")}`);

    // Each value is planted, one at a time, in every kind of browser-facing
    // output: a browser chunk; a prerendered App Router page (HTML and RSC), a
    // route-handler body and its headers; Pages Router HTML and data, including
    // a route that happens to be named like a manifest.
    const html = (value) => `<!DOCTYPE html><p>${value}</p>\n`;
    const json = (value) => `${JSON.stringify({ pageProps: { value } })}\n`;
    const plantings = [
      [path.join(staticDir, "chunks", "planted.js"), (value) => `const leaked = ${JSON.stringify(value)};\n`],
      [path.join(appDir, "x.rsc"), (value) => `1:["$","p",null,{"children":${JSON.stringify(value)}}]\n`],
      [path.join(appDir, "x.html"), html],
      [path.join(appDir, "x.body"), (value) => `${value}\n`],
      [path.join(appDir, "x.meta"), (value) => `${JSON.stringify({ status: 200, headers: { "x-value": value } })}\n`],
      [path.join(pagesDir, "x.html"), html],
      [path.join(pagesDir, "x.json"), json],
      [path.join(pagesDir, "manifest.json"), json],
    ];
    for (const [file, render] of plantings) {
      const where = path.relative(root, file);
      for (const [label, value] of Object.entries(planted)) {
        writeFileSync(file, render(value));
        const found = scan(nextDir, secrets);
        const name = label.trim();
        if (!found.some((finding) => finding.startsWith(`${where}: `) && finding.includes(name))) {
          failures.push(`missed ${name} in ${path.basename(file)}`);
        }
        if (found.some((finding) => finding.includes(value))) failures.push(`printed the value of ${name}`);
        rmSync(file);
      }
    }
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
  if (comparisonProblem([], true) === null) failures.push("--require-values accepted a scan with no values");
  if (comparisonProblem([], false) !== null) failures.push("a scan without --require-values refused no values");
  if (comparisonProblem([["CRON_SECRET", "x".repeat(20)]], true) !== null) {
    failures.push("--require-values refused a scan that had values");
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

const nextDir = path.join(root, "apps/web/.next");
if (!isDirectory(path.join(nextDir, "static"))) {
  console.error("No build output found. Run `pnpm build` first.");
  process.exit(2);
}

const secrets = serverOnlyValues(readEnvFile(), process.env);
const problem = comparisonProblem(secrets, process.argv.includes("--require-values"));
if (problem) {
  console.error(problem);
  process.exit(1);
}
const findings = scan(nextDir, secrets);
if (findings.length) {
  console.error("Server-only values found in browser-facing build output (values not shown):");
  for (const f of findings) console.error(`  - ${f}`);
  process.exit(1);
}
console.log(
  `No server-only values (${secrets.length} checked) or secret-shaped strings found in the browser bundle or prerendered pages.`,
);
