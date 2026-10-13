/**
 * Content Security Policy for the web app (SEC-02).
 *
 * Allows only the origins the template actually uses: the Clerk Frontend API
 * (derived from the publishable key), Clerk's image CDN and bot protection,
 * and — when enabled — PostHog and Sentry ingestion. Next.js requires inline
 * scripts without a nonce setup, and development additionally needs eval.
 */

export type CspInput = {
  clerkPublishableKey: string;
  posthogHost?: string | undefined;
  sentryDsn?: string | undefined;
  isDev: boolean;
};

export function clerkFrontendApiOrigin(publishableKey: string): string | null {
  const match = /^pk_(test|live)_(.+)$/.exec(publishableKey);
  if (!match?.[2]) return null;
  try {
    const host = atob(match[2]).replace(/\$$/, "");
    return /^[a-z0-9.-]+$/i.test(host) ? `https://${host}` : null;
  } catch {
    return null;
  }
}

function originOf(url: string | undefined): string | null {
  if (!url) return null;
  try {
    return new URL(url).origin;
  } catch {
    return null;
  }
}

function sentryIngestOrigin(dsn: string | undefined): string | null {
  if (!dsn) return null;
  try {
    return `https://${new URL(dsn).host}`;
  } catch {
    return null;
  }
}

export function buildContentSecurityPolicy(input: CspInput): string {
  const clerk = clerkFrontendApiOrigin(input.clerkPublishableKey);
  const posthog = originOf(input.posthogHost);
  // PostHog serves its assets from a sibling "-assets" host.
  const posthogAssets = posthog ? posthog.replace(/^(https:\/\/)([a-z]+)\./, "$1$2-assets.") : null;
  const sentry = sentryIngestOrigin(input.sentryDsn);
  const list = (...items: (string | null | false)[]) => items.filter(Boolean).join(" ");

  const directives: Record<string, string> = {
    "default-src": "'self'",
    "script-src": list(
      "'self'",
      "'unsafe-inline'",
      input.isDev && "'unsafe-eval'",
      clerk,
      "https://challenges.cloudflare.com",
      posthogAssets,
    ),
    "connect-src": list(
      "'self'",
      clerk,
      "https://clerk-telemetry.com",
      posthog,
      posthogAssets,
      sentry,
      input.isDev && "ws:",
    ),
    "img-src": list("'self'", "data:", "https://img.clerk.com"),
    "style-src": "'self' 'unsafe-inline'",
    "font-src": "'self' data:",
    "frame-src": list("https://challenges.cloudflare.com"),
    "worker-src": "'self' blob:",
    "form-action": "'self'",
    "base-uri": "'self'",
    "object-src": "'none'",
    "frame-ancestors": "'none'",
  };
  if (!input.isDev) directives["upgrade-insecure-requests"] = "";
  return Object.entries(directives)
    .map(([k, v]) => (v ? `${k} ${v}` : k))
    .join("; ");
}
