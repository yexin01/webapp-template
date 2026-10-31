/**
 * Local Stripe webhooks for `pnpm dev` and `pnpm check:setup` (docs/setup.md#stripe): with
 * BILLING_MODE=sandbox, an sk_test_ key, and the Stripe CLI installed, the CLI
 * supplies the webhook signing secret and forwards events to the API, so nothing
 * is copied by hand. The key reaches the CLI only as STRIPE_API_KEY (never on a
 * command line); the signing secret reaches only the API process, and is never
 * written to .env or printed.
 */
import { execFile, spawn } from "node:child_process";

export const FORWARD_URL = "http://127.0.0.1:8000/api/v1/webhooks/stripe";
const SIGNING_SECRET = /^whsec_[A-Za-z0-9+/=]{16,}$/;

export const MESSAGES = {
  missing:
    "the Stripe CLI is not installed, so Stripe webhooks will not reach this machine (a test checkout stays Free until it is reconciled). Install it (https://docs.stripe.com/stripe-cli), then run this again.",
  failed:
    "the Stripe CLI could not read a webhook signing secret (run `stripe login`, and check that STRIPE_SECRET_KEY is a valid test key), so Stripe webhooks will not reach this machine.",
  invalid: "the Stripe CLI printed something other than a webhook signing secret, so Stripe webhooks will not reach this machine.",
  restrictedKey:
    "BILLING_MODE=sandbox, but STRIPE_SECRET_KEY is not an sk_test_ key, so the Stripe CLI is not started. Forward webhooks yourself: stripe listen --forward-to localhost:8000/api/v1/webhooks/stripe",
};

/** Whether the listener applies, from the API's environment. */
export function listenPlan(env) {
  if ((env.BILLING_MODE ?? "").trim().toLowerCase() !== "sandbox") return { run: false };
  const apiKey = (env.STRIPE_SECRET_KEY ?? "").trim();
  if (apiKey.startsWith("sk_test_")) return { run: true, apiKey };
  // A missing key is a configuration error the preflight reports; a restricted key just cannot start the CLI.
  return apiKey ? { run: false, notice: MESSAGES.restrictedKey } : { run: false };
}

/** Masks anything secret-shaped in a line of CLI output, plus any exact value in `known`. */
export function redact(text, known = []) {
  let result = text.replace(/whsec_[A-Za-z0-9+/=_-]+/g, "whsec_...").replace(/\b[rs]k_(?:test|live)_[A-Za-z0-9_]+/g, "sk_...");
  for (const value of known) if (value) result = result.replaceAll(value, "...");
  return result;
}

/** The CLI's environment: the caller's, plus the key. The key is never an argument. */
export const cliEnv = (apiKey, base = process.env) => ({ ...base, STRIPE_API_KEY: apiKey });

function runStripe(args, env, timeoutMs) {
  return new Promise((resolve) => {
    const child = execFile("stripe", args, { env, timeout: timeoutMs, maxBuffer: 1 << 20 }, (error, stdout) => {
      if (error?.code === "ENOENT") return resolve({ status: "missing" });
      if (error) return resolve({ status: error.killed ? "timeout" : "failed" });
      resolve({ status: "ok", stdout });
    });
    child.stdin?.end();
  });
}

/**
 * Reads the signing secret with `stripe listen --print-secret`, which prints it and exits.
 * @returns {Promise<{status: "ok", secret: string} | {status: "missing" | "failed" | "timeout" | "invalid"}>}
 */
export async function readSigningSecret({ apiKey, run = runStripe, timeoutMs = 15_000, base = process.env }) {
  const result = await run(["listen", "--print-secret"], cliEnv(apiKey, base), timeoutMs);
  if (result.status !== "ok") return { status: result.status };
  const secret = result.stdout.split("\n").map((line) => line.trim()).find((line) => SIGNING_SECRET.test(line));
  return secret ? { status: "ok", secret } : { status: "invalid" };
}

/**
 * Starts `stripe listen --forward-to ...` in its own process group. Output lines
 * go to `onLine` with secrets masked. The caller owns the child (and stops it).
 */
export function startListener({ apiKey, secret, onLine, onExit, spawnImpl = spawn, base = process.env }) {
  const child = spawnImpl("stripe", ["listen", "--forward-to", FORWARD_URL], {
    env: cliEnv(apiKey, base),
    stdio: ["ignore", "pipe", "pipe"],
    detached: true,
  });
  for (const stream of [child.stdout, child.stderr]) {
    let pending = "";
    stream.on("data", (chunk) => {
      const lines = (pending + chunk).split("\n");
      pending = lines.pop() ?? "";
      for (const line of lines) if (line.trim()) onLine(redact(line.trimEnd(), [secret, apiKey]));
    });
  }
  child.on("error", (error) => onExit({ error }));
  child.on("exit", (code, signal) => onExit({ code, signal }));
  return child;
}
