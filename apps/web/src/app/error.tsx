"use client";

import * as Sentry from "@sentry/nextjs";
import { useEffect } from "react";

/** Route-level failure isolation (FE-08). Details are reported, never shown. */
export default function RouteError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    Sentry.captureException(error);
  }, [error]);

  return (
    <section className="state state-error" role="alert">
      <h1>Something went wrong</h1>
      <p>This part of the page failed to load. You can try again.</p>
      {error.digest && <p className="muted">Reference: {error.digest}</p>}
      <button type="button" className="button" onClick={reset}>
        Try again
      </button>
    </section>
  );
}
