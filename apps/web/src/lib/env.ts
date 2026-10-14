/**
 * Public configuration (ENV-03): the only values the browser bundle receives.
 *
 * Next.js inlines each `process.env.NEXT_PUBLIC_*` read below at build time.
 * NEXT_PUBLIC_APP_ENV and the NEXT_PUBLIC_*_ENABLED flags are derived from
 * server settings by next.config.ts. Every value was validated when
 * next.config.ts loaded (see env.server.ts), so this module only types them.
 * Server-only settings belong in env.server.ts, never here.
 */

export const APP_ENVS = ["local", "test", "preview", "production"] as const;
export type AppEnv = (typeof APP_ENVS)[number];

export const DEFAULT_POSTHOG_HOST = "https://us.i.posthog.com";

export function isAppEnv(value: string | undefined): value is AppEnv {
  return (APP_ENVS as readonly (string | undefined)[]).includes(value);
}

export type PublicEnv = Readonly<{
  appEnv: AppEnv;
  clerkPublishableKey: string;
  analytics: Readonly<{ enabled: boolean; key: string; host: string }>;
  errorReporting: Readonly<{ enabled: boolean; dsn: string }>;
}>;

const posthogKey = process.env.NEXT_PUBLIC_POSTHOG_KEY ?? "";
const sentryDsn = process.env.NEXT_PUBLIC_SENTRY_DSN ?? "";

export const publicEnv: PublicEnv = Object.freeze({
  appEnv: isAppEnv(process.env.NEXT_PUBLIC_APP_ENV) ? process.env.NEXT_PUBLIC_APP_ENV : "local",
  clerkPublishableKey: process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY ?? "",
  analytics: Object.freeze({
    enabled: process.env.NEXT_PUBLIC_ANALYTICS_ENABLED === "true" && Boolean(posthogKey),
    key: posthogKey,
    host: process.env.NEXT_PUBLIC_POSTHOG_HOST || DEFAULT_POSTHOG_HOST,
  }),
  errorReporting: Object.freeze({
    enabled: process.env.NEXT_PUBLIC_ERROR_REPORTING_ENABLED === "true" && Boolean(sentryDsn),
    dsn: sentryDsn,
  }),
});
