import path from "node:path";
import { fileURLToPath } from "node:url";

import { loadEnvConfig } from "@next/env";
import { withSentryConfig } from "@sentry/nextjs/config";
import type { NextConfig } from "next";

import { buildContentSecurityPolicy } from "./src/lib/csp";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
// One repository-root .env serves both apps (see .env.example).
loadEnvConfig(repoRoot, process.env.NODE_ENV !== "production");

const isDev = process.env.NODE_ENV !== "production";
const apiOrigin = (process.env.API_ORIGIN ?? "http://127.0.0.1:8000").replace(/\/$/, "");
const enabled = (mode: string | undefined) => mode === "sandbox" || mode === "live";

const csp = buildContentSecurityPolicy({
  clerkPublishableKey: process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY ?? "",
  posthogHost: enabled(process.env.ANALYTICS_MODE) ? process.env.NEXT_PUBLIC_POSTHOG_HOST : undefined,
  sentryDsn: enabled(process.env.ERROR_REPORTING_MODE) ? process.env.NEXT_PUBLIC_SENTRY_DSN : undefined,
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

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // Expose only derived, non-secret flags to the browser bundle.
  env: {
    NEXT_PUBLIC_ANALYTICS_ENABLED: String(enabled(process.env.ANALYTICS_MODE)),
    NEXT_PUBLIC_ERROR_REPORTING_ENABLED: String(enabled(process.env.ERROR_REPORTING_MODE)),
    NEXT_PUBLIC_APP_ENV: process.env.APP_ENV ?? "local",
  },
  async rewrites() {
    // The browser calls the Python API same-origin; no CORS and no API secrets in the web app.
    return [{ source: "/api/v1/:path*", destination: `${apiOrigin}/api/v1/:path*` }];
  },
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default withSentryConfig(nextConfig, {
  silent: true,
  telemetry: false,
  // Source maps upload only when a CI-only auth token is present.
  sourcemaps: { disable: !process.env.SENTRY_AUTH_TOKEN },
});
