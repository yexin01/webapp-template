/**
 * The environment of `pnpm build:check` (scripts/build-check.mjs): an account-free build that is the same on
 * every machine. It is built from an allowlist, not from the caller's environment: the machine variables that
 * find and start the tools, and the values set here. It gets the placeholder Clerk publishable key that
 * .env.example ships, a synthetic secret key and a synthetic public key (CLERK_JWT_KEY, which the proxy bundle
 * inlines) made up for the run, so the bundle scan proves the server-only values the web build can see stay
 * out of the browser bundle. Telemetry is off, APP_ENV is local, and SKIP_ROOT_ENV_FILE keeps next.config.ts
 * from reading the root .env. Nothing else the shell exports reaches the build, so SENTRY_AUTH_TOKEN cannot
 * switch on a source-map upload and API_ORIGIN, NEXT_PUBLIC_* or VERCEL* cannot change the output.
 */
import { generateKeyPairSync, randomBytes } from "node:crypto";

import { PLACEHOLDER_PUBLISHABLE_KEY } from "./clerk.mjs";

// What the tools need from the machine to be found and started, compared in upper case (Windows folds case).
const MACHINE_VARIABLES = new Set([
  "PATH",
  "HOME",
  "TMPDIR",
  "TMP",
  "TEMP",
  "PNPM_HOME",
  // Windows: cmd.exe, the profile folders, and the program folders.
  "PATHEXT",
  "SYSTEMROOT",
  "SYSTEMDRIVE",
  "WINDIR",
  "COMSPEC",
  "USERPROFILE",
  "APPDATA",
  "LOCALAPPDATA",
  "HOMEDRIVE",
  "HOMEPATH",
  "PROGRAMFILES",
  "PROGRAMFILES(X86)",
  "PROGRAMDATA",
  "NUMBER_OF_PROCESSORS",
  "PROCESSOR_ARCHITECTURE",
]);
// Corepack and the Node version managers keep their shims working from variables of their own.
const MANAGER_PREFIXES = ["COREPACK_", "VOLTA_", "ASDF_", "MISE_", "NVM_", "FNM_"];

const fromMachine = (name) => {
  const upper = name.toUpperCase();
  return MACHINE_VARIABLES.has(upper) || MANAGER_PREFIXES.some((prefix) => upper.startsWith(prefix));
};

export function buildCheckEnv(base = process.env) {
  return {
    ...Object.fromEntries(Object.entries(base).filter(([name]) => fromMachine(name))),
    APP_ENV: "local",
    ANALYTICS_MODE: "disabled",
    ERROR_REPORTING_MODE: "disabled",
    SKIP_ROOT_ENV_FILE: "1",
    NEXT_TELEMETRY_DISABLED: "1",
    CLERK_JWT_KEY: generateKeyPairSync("rsa", { modulusLength: 2048 }).publicKey.export({ type: "spki", format: "pem" }),
    NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: PLACEHOLDER_PUBLISHABLE_KEY,
    CLERK_SECRET_KEY: `sk_test_${randomBytes(24).toString("hex")}`,
  };
}
