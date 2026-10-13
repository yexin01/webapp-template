import * as Sentry from "@sentry/nextjs";

export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs" || process.env.NEXT_RUNTIME === "edge") {
    await import("./sentry.server.config");
  }
}

export const onRequestError = Sentry.captureRequestError;
