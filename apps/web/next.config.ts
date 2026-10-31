import path from "node:path";
import { fileURLToPath } from "node:url";

import { withSentryConfig } from "@sentry/nextjs/config";
import type { NextConfig } from "next";
import { PHASE_DEVELOPMENT_SERVER, PHASE_PRODUCTION_BUILD, PHASE_PRODUCTION_SERVER } from "next/constants";

import { PROXY_TIMEOUT_MS } from "./src/lib/api/deadlines";
import { buildContentSecurityPolicy } from "./src/lib/csp";
import { loadEnvFiles, strayEnvFiles } from "./src/lib/env-files";
import {
  ConfigError,
  isWebVariable,
  loadServerEnv,
  sentryBuildOptions,
  sourceMapUploadProblems,
} from "./src/lib/env.server";

const webDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(webDir, "../..");
const isDev = process.env.NODE_ENV !== "production";
// One repository-root .env serves both apps (see .env.example). This loader copies
// only the variables the web app reads (env.server.ts), so a local run never gives
// API secrets such as STRIPE_SECRET_KEY and DATABASE_URL to the web process. On
// Vercel the platform sets the process environment, and one project gives every
// service every variable (docs/deployment.md). `pnpm build:check` sets SKIP_ROOT_ENV_FILE so that
// its build is the same on every machine whatever the .env holds.
if (process.env.SKIP_ROOT_ENV_FILE !== "1") loadEnvFiles(repoRoot, isWebVariable, process.env);

function nextConfig(phase: string): NextConfig {
  const stray = strayEnvFiles(repoRoot, webDir);
  if (stray.length) {
    throw new ConfigError([
      `Only the repository-root .env is read (API, web app, bundle scan). Move the settings in ${stray.join(", ")} into it and delete those files. ` +
        "apps/web/.env* files are loaded by Next.js itself, unfiltered, and the API never sees them.",
    ]);
  }
  // ENV-03: fail fast, naming the variable but never its value.
  const env = loadServerEnv(process.env, {
    serving: phase === PHASE_DEVELOPMENT_SERVER || phase === PHASE_PRODUCTION_SERVER,
  });
  if (phase === PHASE_PRODUCTION_BUILD) {
    const uploadProblems = sourceMapUploadProblems(process.env);
    if (uploadProblems.length) throw new ConfigError(uploadProblems);
  }

  const csp = buildContentSecurityPolicy({
    clerkPublishableKey: env.clerkPublishableKey,
    posthogHost: env.analytics.enabled ? env.analytics.host : undefined,
    sentryDsn: env.errorReporting.enabled ? env.errorReporting.dsn : undefined,
    isDev,
  });

  const securityHeaders = [
    { key: "Content-Security-Policy", value: csp },
    { key: "X-Content-Type-Options", value: "nosniff" },
    { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
    { key: "X-Frame-Options", value: "DENY" },
    { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
    ...(isDev ? [] : [{ key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" }]),
  ];

  return {
    reactStrictMode: true,
    poweredByHeader: false,
    experimental: {
      // The /api/v1 rewrite proxy of `next dev` and `next start` would otherwise give up after
      // 30 s with a bare 500, before the client's 90 s provider deadline (src/lib/api/deadlines.ts).
      proxyTimeout: PROXY_TIMEOUT_MS,
    },
    // Expose only derived, non-secret flags to the browser bundle (read through src/lib/env.ts).
    env: {
      NEXT_PUBLIC_ANALYTICS_ENABLED: String(env.analytics.enabled),
      NEXT_PUBLIC_ERROR_REPORTING_ENABLED: String(env.errorReporting.enabled),
      NEXT_PUBLIC_APP_ENV: env.appEnv,
      NEXT_PUBLIC_STRIPE_CUSTOM_DOMAIN: env.stripeCustomDomain ?? "",
      // Origins, not secrets: src/proxy.ts reads them where it may not read the server-only variables they come from.
      NEXT_PUBLIC_CLERK_AUTHORIZED_PARTIES: env.clerkAuthorizedParties.join(","),
      // Clerk's public key, read by src/proxy.ts for the same reason. Only the proxy references it, so no browser bundle has it.
      NEXT_PUBLIC_CLERK_JWT_KEY: env.clerkJwtKey,
    },
    async rewrites() {
      // The browser calls the Python API same-origin; no CORS and no API secrets in the web app.
      return [{ source: "/api/v1/:path*", destination: `${env.apiOrigin}/api/v1/:path*` }];
    },
    async headers() {
      return [{ source: "/:path*", headers: securityHeaders }];
    },
  };
}

// Source maps upload only when SENTRY_AUTH_TOKEN is present in the build environment
// (deliberately not loaded from the root .env); `next build` then needs SENTRY_PROJECT too.
export default withSentryConfig(nextConfig, sentryBuildOptions(process.env));
