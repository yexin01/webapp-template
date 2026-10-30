/**
 * Server configuration for the web app (ENV-02 to ENV-04): every variable the
 * Next.js server reads, public or server-only, validated in one place.
 *
 * next.config.ts calls `loadServerEnv(process.env, ...)` when it loads, so
 * `next build`, `next dev`, and `next start` fail fast on a missing, malformed,
 * or inconsistent setting. Messages name the variable and never its value.
 * The rules mirror the API's (apps/api: kernel config and identity settings)
 * for the variables both apps read: the environment (APP_ENV and the platform's
 * VERCEL_ENV), the Clerk keys, the telemetry modes and keys. Unlike the API,
 * which refuses a bundle without APP_ENV by layout, the web app is built inside
 * a full checkout, so VERCEL=1 marks a Vercel build or deployment: there it
 * also requires CLERK_SECRET_KEY, because Vercel serves the build output and
 * `next start` never runs to check it.
 *
 * Server-only: import it only from next.config.ts and server instrumentation
 * (enforced by eslint.guards.mjs). It reads no global state; callers pass the
 * environment in. Browser code uses the public half, env.ts.
 */
import { clerkFrontendApiOrigin } from "./csp";
import { DEFAULT_POSTHOG_HOST, isAppEnv, type AppEnv } from "./env";

/** Server-only variables the web app reads. next.config.ts loads only these and NEXT_PUBLIC_* from the root .env. */
export const SERVER_VARIABLES = [
  "APP_ENV",
  "VERCEL_ENV",
  "API_ORIGIN",
  "ANALYTICS_MODE",
  "ERROR_REPORTING_MODE",
  "SENTRY_TRACES_SAMPLE_RATE",
  "CLERK_SECRET_KEY",
  "CLERK_JWT_KEY",
  "STRIPE_CUSTOM_DOMAIN",
] as const;

export function isWebVariable(name: string): boolean {
  return name.startsWith("NEXT_PUBLIC_") || (SERVER_VARIABLES as readonly string[]).includes(name);
}

const VERCEL_ENVS = ["production", "preview", "development"] as const;
type VercelEnv = (typeof VERCEL_ENVS)[number];

/** What an unset APP_ENV is on a Vercel deployment (ENV-01); the API derives the same. */
const APP_ENV_FROM_PLATFORM: Readonly<Record<VercelEnv, AppEnv>> = {
  production: "production",
  preview: "preview",
  development: "local",
};

const TELEMETRY_MODES = ["disabled", "sandbox", "live"] as const;
export type TelemetryMode = (typeof TELEMETRY_MODES)[number];

export type ServerEnv = Readonly<{
  appEnv: AppEnv;
  apiOrigin: string;
  clerkPublishableKey: string;
  /** The Stripe custom domain of Checkout and the Customer Portal, if the account has one. */
  stripeCustomDomain: string | null;
  analytics: Readonly<{ mode: TelemetryMode; enabled: boolean; host: string }>;
  errorReporting: Readonly<{ mode: TelemetryMode; enabled: boolean; dsn: string; tracesSampleRate: number }>;
}>;

export type LoadOptions = {
  /** `next dev` and `next start`: also require what serving requests needs (CLERK_SECRET_KEY). */
  serving: boolean;
};

export class ConfigError extends Error {
  constructor(readonly problems: readonly string[]) {
    super(`Invalid web configuration:\n- ${problems.join("\n- ")}`);
    this.name = "ConfigError";
  }
}

type Source = Readonly<Record<string, string | undefined>>;

const PLACEHOLDER = /replace_me/i;

function isHttpUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:";
  } catch {
    return false;
  }
}

/**
 * An http(s) origin: scheme, host, and optional port, with at most a bare
 * trailing slash. A path, query, fragment, or credentials would be spliced
 * into the /api/v1 rewrite destination, so they are rejected, not dropped.
 */
function httpOrigin(value: string): string | null {
  if (/[?#]/.test(value)) return null;
  try {
    const url = new URL(value);
    const http = url.protocol === "https:" || url.protocol === "http:";
    return http && url.pathname === "/" && !url.username && !url.password ? url.origin : null;
  } catch {
    return null;
  }
}

/** This machine (localhost, 127.0.0.0/8, or [::1]), where plain http never crosses a network. */
function isLoopbackOrigin(origin: string): boolean {
  const { hostname } = new URL(origin);
  return hostname === "localhost" || hostname === "[::1]" || /^127(?:\.\d{1,3}){3}$/.test(hostname);
}

/** A bare lowercase hostname with at least one dot: no scheme, port, path, or wildcard (like the API's check). */
const HOSTNAME = /^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/;

/** A Sentry DSN is an http(s) URL whose user part is the project's public key. */
function isSentryDsn(value: string): boolean {
  return isHttpUrl(value) && new URL(value).username.length > 0;
}

function telemetryMode(
  name: string,
  raw: string | undefined,
  appEnv: AppEnv,
  problems: string[],
): TelemetryMode {
  if (raw === undefined && (appEnv === "preview" || appEnv === "production")) {
    problems.push(`${name} is required when APP_ENV=${appEnv}`);
  }
  const mode = TELEMETRY_MODES.find((known) => known === (raw ?? "disabled"));
  if (!mode) {
    // The raw value is not echoed: an unexpected value may be a pasted secret.
    problems.push(`${name} must be disabled, sandbox, or live`);
    return "disabled";
  }
  // ENV-02: live only in production, and production never runs sandbox telemetry.
  if (mode === "live" && appEnv !== "production") {
    problems.push(`${name}=live is only allowed when APP_ENV=production`);
  }
  if (mode === "sandbox" && appEnv === "production") {
    problems.push(`${name}=sandbox is not allowed when APP_ENV=production`);
  }
  return mode;
}

/** ENV-01 for the platform: the application environment may not contradict Vercel's (names and labels only). */
function platformProblems(appEnv: AppEnv, vercelEnv: VercelEnv | undefined): string[] {
  if (appEnv === "production" && (vercelEnv === "preview" || vercelEnv === "development")) {
    return [`APP_ENV=production is not allowed when VERCEL_ENV=${vercelEnv}`];
  }
  if ((appEnv === "local" || appEnv === "test") && (vercelEnv === "production" || vercelEnv === "preview")) {
    const allowed = vercelEnv === "production" ? "preview or production" : "preview";
    return [`APP_ENV=${appEnv} is not allowed when VERCEL_ENV=${vercelEnv} (set ${allowed})`];
  }
  return [];
}

/** ENV-04 for Clerk: live keys only in production, and only live keys there. */
function clerkProblems(source: (name: string) => string | undefined, appEnv: AppEnv, serving: boolean) {
  const problems: string[] = [];
  const production = appEnv === "production";
  const publishable = source("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY");
  const secret = source("CLERK_SECRET_KEY");
  for (const [name, value] of [
    ["NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", publishable],
    ["CLERK_SECRET_KEY", secret],
  ] as const) {
    if (!value) continue;
    const live = value.startsWith("pk_live_") || value.startsWith("sk_live_");
    if (live && !production)
      problems.push(`${name} is a live key; live keys are only allowed when APP_ENV=production`);
    if (!live && production) problems.push(`${name} must be a live key when APP_ENV=production`);
  }
  const jwtKey = source("CLERK_JWT_KEY")?.replaceAll("\\n", "\n");
  if (jwtKey && !/^-----BEGIN PUBLIC KEY-----\s[\s\S]+\s-----END PUBLIC KEY-----$/.test(jwtKey)) {
    problems.push("CLERK_JWT_KEY must be a PEM-encoded public key");
  }
  // Like the API, automated tests (APP_ENV=test) need no Clerk instance.
  if (appEnv === "test") return problems;

  // The CSP and the Clerk SDK derive the Frontend API origin from this key; a
  // placeholder such as pk_test_replace_me would silently drop it from the CSP.
  if (!publishable) problems.push("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is required");
  else if (PLACEHOLDER.test(publishable))
    problems.push(
      "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is still the .env.example placeholder (copy it from Clerk Dashboard > API keys)",
    );
  else if (!clerkFrontendApiOrigin(publishable))
    problems.push("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is malformed");
  if (secret && (!/^sk_(test|live)_\S+$/.test(secret) || PLACEHOLDER.test(secret))) {
    problems.push("CLERK_SECRET_KEY is malformed or still the .env.example placeholder");
  }
  if (!secret && serving) problems.push("CLERK_SECRET_KEY is required");
  return problems;
}

/**
 * PostHog's project token (phc_...) is public and ships in every browser bundle, whatever
 * ANALYTICS_MODE says. Its personal, secret, and OAuth keys give account access and must never
 * get there. The value is never echoed.
 */
function posthogKeyProblems(key: string | undefined): string[] {
  if (!key) return [];
  if (/^ph[xsar]_/.test(key)) {
    return [
      "NEXT_PUBLIC_POSTHOG_KEY holds a secret PostHog credential (personal, secret or OAuth key); use the project token (phc_...) from Project settings",
    ];
  }
  return /^phc_\S+$/.test(key) ? [] : ["NEXT_PUBLIC_POSTHOG_KEY must be a PostHog project token (phc_...)"];
}

/**
 * Reads and validates the web app's configuration. Throws a ConfigError that
 * lists every problem by variable name. An empty value means "not set".
 */
export function loadServerEnv(env: Source, options: LoadOptions): ServerEnv {
  const source = (name: string) => env[name]?.trim() || undefined;
  const problems: string[] = [];

  // VERCEL=1 marks every Vercel build and deployment, and nothing else (the account-free CI build has none).
  const vercel = source("VERCEL") === "1";
  const rawVercelEnv = source("VERCEL_ENV");
  const vercelEnv = VERCEL_ENVS.find((known) => known === rawVercelEnv);
  if (rawVercelEnv !== undefined && !vercelEnv) {
    problems.push("VERCEL_ENV must be production, preview, or development");
  }

  const rawAppEnv = source("APP_ENV") ?? (vercelEnv ? APP_ENV_FROM_PLATFORM[vercelEnv] : undefined);
  if (rawAppEnv === undefined && vercel)
    problems.push("APP_ENV is required on Vercel (set preview or production)");
  const requestedAppEnv = rawAppEnv ?? "local";
  const appEnv: AppEnv = isAppEnv(requestedAppEnv) ? requestedAppEnv : "local";
  if (!isAppEnv(requestedAppEnv)) problems.push("APP_ENV must be local, test, preview, or production");
  else problems.push(...platformProblems(appEnv, vercelEnv));

  const apiOrigin = httpOrigin(source("API_ORIGIN") ?? "http://127.0.0.1:8000");
  if (!apiOrigin) {
    problems.push("API_ORIGIN must be an http(s) origin, with no path, query, fragment, or credentials");
  } else if (appEnv === "production" && apiOrigin.startsWith("http:") && !isLoopbackOrigin(apiOrigin)) {
    // The /api/v1 rewrite forwards every session token there (the API's APP_BASE_URL has the same rule).
    problems.push("API_ORIGIN must use https in production");
  }

  // Vercel serves the build output and never runs `next start`, so its build is where a missing secret surfaces.
  problems.push(...clerkProblems(source, appEnv, options.serving || vercel));

  const stripeCustomDomain = source("STRIPE_CUSTOM_DOMAIN") ?? null;
  if (stripeCustomDomain !== null && !HOSTNAME.test(stripeCustomDomain)) {
    problems.push("STRIPE_CUSTOM_DOMAIN must be a hostname such as payments.example.com");
  }

  const analyticsMode = telemetryMode("ANALYTICS_MODE", source("ANALYTICS_MODE"), appEnv, problems);
  const analyticsEnabled = analyticsMode !== "disabled";
  if (analyticsEnabled && !source("NEXT_PUBLIC_POSTHOG_KEY")) {
    problems.push("NEXT_PUBLIC_POSTHOG_KEY is required when ANALYTICS_MODE is enabled");
  }
  problems.push(...posthogKeyProblems(source("NEXT_PUBLIC_POSTHOG_KEY")));
  const posthogHost = source("NEXT_PUBLIC_POSTHOG_HOST") ?? DEFAULT_POSTHOG_HOST;
  if (!isHttpUrl(posthogHost)) problems.push("NEXT_PUBLIC_POSTHOG_HOST must be an absolute http(s) URL");

  const errorReportingMode = telemetryMode(
    "ERROR_REPORTING_MODE",
    source("ERROR_REPORTING_MODE"),
    appEnv,
    problems,
  );
  const errorReportingEnabled = errorReportingMode !== "disabled";
  const dsn = source("NEXT_PUBLIC_SENTRY_DSN") ?? "";
  if (errorReportingEnabled && !dsn) {
    problems.push("NEXT_PUBLIC_SENTRY_DSN is required when ERROR_REPORTING_MODE is enabled");
  }
  if (dsn && !isSentryDsn(dsn))
    problems.push("NEXT_PUBLIC_SENTRY_DSN must be a Sentry DSN (https://<key>@<host>/<id>)");

  const rawRate = source("SENTRY_TRACES_SAMPLE_RATE") ?? "0";
  const tracesSampleRate = Number(rawRate);
  if (!/^\d*\.?\d+$/.test(rawRate) || !(tracesSampleRate >= 0 && tracesSampleRate <= 1)) {
    problems.push("SENTRY_TRACES_SAMPLE_RATE must be a number from 0 to 1");
  }

  if (problems.length || !apiOrigin) throw new ConfigError(problems);
  return Object.freeze({
    appEnv,
    apiOrigin,
    clerkPublishableKey: source("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY") ?? "",
    stripeCustomDomain,
    analytics: Object.freeze({ mode: analyticsMode, enabled: analyticsEnabled, host: posthogHost }),
    errorReporting: Object.freeze({
      mode: errorReportingMode,
      enabled: errorReportingEnabled,
      dsn,
      tracesSampleRate,
    }),
  });
}

/**
 * What source-map upload to Sentry needs, checked by `next build` only. SENTRY_AUTH_TOKEN,
 * SENTRY_ORG, and SENTRY_PROJECT come from the build environment (Vercel or CI), never from the
 * root .env, so they are not in SERVER_VARIABLES. Without a project, or without an organization
 * (an organization token, sntrys_..., carries its own), the plugin skips the upload with a
 * warning that `silent` would hide. Names only, never a value.
 */
export function sourceMapUploadProblems(env: Source): string[] {
  const token = env.SENTRY_AUTH_TOKEN?.trim();
  if (!token) return [];
  const problems: string[] = [];
  if (!env.SENTRY_PROJECT?.trim()) {
    problems.push("SENTRY_PROJECT (the web project's slug) is required when SENTRY_AUTH_TOKEN is set");
  }
  if (!env.SENTRY_ORG?.trim() && !token.startsWith("sntrys_")) {
    problems.push("SENTRY_ORG is required unless SENTRY_AUTH_TOKEN is an organization token (sntrys_...)");
  }
  return problems;
}

/**
 * The build options for withSentryConfig. The plugin reads SENTRY_ORG and SENTRY_PROJECT itself.
 * It is silent only when no upload is configured: otherwise its warnings (a skipped upload) and
 * its upload errors and success line belong in the build log.
 */
export function sentryBuildOptions(env: Source) {
  const upload = Boolean(env.SENTRY_AUTH_TOKEN?.trim());
  return { silent: !upload, telemetry: false, sourcemaps: { disable: !upload } } as const;
}
