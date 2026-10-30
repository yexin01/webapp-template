/**
 * The configuration preflight of `pnpm dev` and `pnpm check:setup`: it asks both apps
 * to validate the configuration they would start with, so a mistake is one short
 * message here instead of a traceback from a server that dies at import. It
 * calls the apps' own validators (the API's `check-config`, and the web app's
 * next.config.ts through Next's loader) and duplicates none of their rules.
 */
import { spawn } from "node:child_process";
import path from "node:path";

import { clerkKeyState } from "./clerk.mjs";
import { readDotenv } from "./env.mjs";

const HELP = "Edit .env (see .env.example and docs/integrations.md), then run this again.";

/** Runs a command to completion. Resolves with its exit code and output; `missing` when it is not installed. */
export function capture(command, args, { cwd, env, timeoutMs = 120_000 }) {
  return new Promise((resolve) => {
    const child = spawn(command, args, { cwd, env, stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => child.kill("SIGKILL"), timeoutMs);
    child.stdout.on("data", (chunk) => (stdout += chunk));
    child.stderr.on("data", (chunk) => (stderr += chunk));
    child.on("error", (error) => {
      clearTimeout(timer);
      resolve({ code: null, stdout, stderr, missing: error.code === "ENOENT" });
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      resolve({ code, stdout, stderr, missing: false });
    });
  });
}

/** The problem lines of `check-config`: "  - NAME ..." on stderr. */
export function parseProblems(stderr) {
  return stderr
    .split("\n")
    .filter((line) => line.startsWith("  - "))
    .map((line) => line.slice(4).trim());
}

/** The API's check, from its exit status: 0 valid, 1 invalid settings, anything else a command that did not run. */
export function apiProblems({ code, stderr, missing }) {
  if (missing) return ["uv is not installed (https://docs.astral.sh/uv/), so the API cannot be checked or started"];
  if (code === 0) return [];
  const problems = parseProblems(stderr);
  if (code === 1 && problems.length) return problems;
  const last = stderr.trim().split("\n").pop() ?? "";
  return [`the API configuration could not be checked (${last.slice(0, 200) || `exit ${code}`}); run pnpm bootstrap`];
}

/** The web app's check: the `WEBCONFIG:` line of check-web-config.mjs. */
export function webProblems({ stdout, code }) {
  const line = stdout.split("\n").findLast((text) => text.startsWith("WEBCONFIG:"));
  if (!line) return code === 0 ? [] : ["the web configuration could not be checked; run pnpm bootstrap"];
  const result = JSON.parse(line.slice("WEBCONFIG:".length));
  return result.ok === false ? result.problems : [];
}

const CLERK_LEGACY =
  "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY and CLERK_SECRET_KEY still hold the old `replace_me` placeholders: paste your Clerk development keys, or copy the current placeholder pair from .env.example to run without an account";
const CLERK_PLACEHOLDER =
  "Clerk is not configured (the placeholder keys from .env.example), so sign-in is unavailable. Paste your Clerk development keys into .env (docs/integrations.md#clerk).";
// Clerk's development mode sends every page request to its Frontend API first, which a placeholder key cannot reach.
const CLERK_NEEDED_FOR_PAGES =
  "Clerk is not configured (the placeholder keys from .env.example). The web app cannot load a page without a Clerk instance: create a free development instance and paste its two keys into .env (docs/integrations.md#clerk).";

/**
 * @returns {Promise<{ok: boolean, problems: string[], warnings: string[]}>}
 * `env` is what the API will see (process environment over .env, plus anything
 * the caller adds); `childEnv` is the environment the checks run in. With
 * `requireClerk` (starting the web app), the placeholder Clerk pair is a problem, not a warning.
 */
export async function runPreflight({ root, env, childEnv, run = capture, requireClerk = false }) {
  const problems = [];
  const warnings = [];
  if (readDotenv(root) === null && !env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY) {
    return {
      ok: false,
      problems: ["there is no .env file: run `pnpm init-template` (it creates one), or `cp .env.example .env`"],
      warnings,
    };
  }
  const clerk = clerkKeyState(env);
  if (clerk === "legacy-placeholder" || clerk === "missing") problems.push(CLERK_LEGACY);
  if (clerk === "placeholder") (requireClerk ? problems : warnings).push(requireClerk ? CLERK_NEEDED_FOR_PAGES : CLERK_PLACEHOLDER);

  const [api, web] = await Promise.all([
    run("uv", ["run", "python", "-m", "app.cli", "check-config"], { cwd: path.join(root, "apps/api"), env: childEnv }),
    // Next resolves next.config.ts's relative imports from the working directory.
    run(process.execPath, [path.join(root, "scripts/lib/check-web-config.mjs"), path.join(root, "apps/web")], {
      cwd: path.join(root, "apps/web"),
      env: { ...childEnv, NEXT_TELEMETRY_DISABLED: "1" },
    }),
  ]);
  // The message about the old placeholders already names both keys.
  const covered = (problem) => clerk === "legacy-placeholder" && /^(?:NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY|CLERK_SECRET_KEY) /.test(problem);
  for (const problem of [...apiProblems(api), ...webProblems(web)]) {
    if (!problems.includes(problem) && !covered(problem)) problems.push(problem);
  }
  return { ok: problems.length === 0, problems, warnings };
}

/** The lines to print for a failed preflight, under the name of the command. */
export function describeFailure(command, problems) {
  return [`${command}: the configuration has problems.`, ...problems.map((problem) => `  - ${problem}`), HELP].join("\n");
}
