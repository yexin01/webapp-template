import path from "node:path";
import { fileURLToPath } from "node:url";

import { withSentryConfig } from "@sentry/nextjs/config";
import type { NextConfig } from "next";
import { PHASE_DEVELOPMENT_SERVER, PHASE_PRODUCTION_SERVER } from "next/constants";

import { PROXY_TIMEOUT_MS } from "./src/lib/api/deadlines";
import { buildContentSecurityPolicy } from "./src/lib/csp";
import { loadEnvFiles } from "./src/lib/env-files";
import { isWebVariable, loadServerEnv } from "./src/lib/env.server";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const isDev = process.env.NODE_ENV !== "production";
// One repository-root .env serves both apps (see .env.example). This process
// loads only the variables the web app reads (env.server.ts), so API secrets
// such as STRIPE_SECRET_KEY and DATABASE_URL never enter it.
loadEnvFiles(
  repoRoot,
  process.env.NODE_ENV === "test" ? "test" : isDev ? "development" : "production",
  isWebVariable,
  process.env,
);

function nextConfig(phase: string): NextConfig {
  // ENV-03: fail fast, naming the variable but never its value.
  const env = loadServerEnv(process.env, {
    serving: phase === PHASE_DEVELOPMENT_SERVER || phase === PHASE_PRODUCTION_SERVER,
  });

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

export default withSentryConfig(nextConfig, {
  silent: true,
  telemetry: false,
  // Source maps upload only when a CI-only auth token is present in the build
  // environment (it is deliberately not loaded from the root .env).
  sourcemaps: { disable: !process.env.SENTRY_AUTH_TOKEN },
});
