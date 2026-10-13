#!/usr/bin/env node
// ENV-07: the browser bundle must not contain the values of server-only
// variables or secret-shaped strings. Run after `pnpm build`.
//
// Variable *names* may legitimately appear inside SDK code (for example,
// Clerk's shared code reads process.env.CLERK_SECRET_KEY, which is undefined
// in browsers), so the check compares actual values. Values are read from the
// environment and the root .env file and are never printed.
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const staticDir = path.join(root, "apps/web/.next/static");

const SERVER_ONLY_NAMES = [
  "CLERK_SECRET_KEY",
  "CLERK_WEBHOOK_SIGNING_SECRET",
  "CLERK_JWT_KEY",
  "STRIPE_SECRET_KEY",
  "STRIPE_WEBHOOK_SECRET",
  "DATABASE_URL",
  "LLM_API_KEY",
  "SENTRY_AUTH_TOKEN",
];
const SECRET_PATTERNS = [
  /\b[sr]k_(?:live|test)_[A-Za-z0-9]{10,}/,
  /\bwhsec_[A-Za-z0-9+/=]{10,}/,
  /postgres(?:ql)?:\/\/[^\s"']+:[^\s"'@]+@/,
  /-----BEGIN [A-Z ]*PRIVATE KEY-----/,
];

function* files(dir) {
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry);
    if (statSync(full).isDirectory()) yield* files(full);
    else if (/\.(js|css|json|html|map)$/.test(entry)) yield full;
  }
}

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

function serverOnlyValues() {
  const values = new Map();
  try {
    for (const line of readFileSync(path.join(root, ".env"), "utf8").split(/\r?\n/)) {
      const match = /^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/.exec(line);
      if (match && SERVER_ONLY_NAMES.includes(match[1])) values.set(match[1], match[2].replace(/^["']|["']$/g, ""));
    }
  } catch {
    /* no .env file */
  }
  for (const name of SERVER_ONLY_NAMES) if (process.env[name]) values.set(name, process.env[name]);
  // Ignore empty and very short values to avoid meaningless matches.
  return [...values].filter(([, value]) => value.length >= 12);
}

const secrets = serverOnlyValues();
const findings = [];
for (const file of files(staticDir)) {
  const text = readFileSync(file, "utf8");
  for (const [name, value] of secrets) if (text.includes(value)) findings.push(`${path.relative(root, file)}: value of ${name}`);
  for (const pattern of SECRET_PATTERNS)
    if (pattern.test(text)) findings.push(`${path.relative(root, file)}: matches ${pattern}`);
}
if (findings.length) {
  console.error("Server-only values found in the browser bundle (values not shown):");
  for (const f of findings) console.error(`  - ${f}`);
  process.exit(1);
}
console.log(`No server-only values (${secrets.length} checked) or secret-shaped strings found in the browser bundle.`);
