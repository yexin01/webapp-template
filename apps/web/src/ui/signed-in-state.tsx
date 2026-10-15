"use client";

import { Show } from "@clerk/nextjs";
import type { ReactNode } from "react";

import { reloadPage, useClerkLoadTimedOut } from "@/lib/use-resource";

/**
 * Signed-in and signed-out switches for server components, decided in the
 * browser. Clerk's server-side <Show> calls auth(), which throws on every
 * request the proxy does not run on (static-file paths such as /favicon.ico,
 * /api/v1…, /_next…), turning those 404s into 500s, and it makes every page
 * dynamic. These render nothing until Clerk has loaded in the browser.
 */
export function SignedInOnly({ children }: { children: ReactNode }) {
  return <Show when="signed-in">{children}</Show>;
}

export function SignedOutOnly({ children }: { children: ReactNode }) {
  return <Show when="signed-out">{children}</Show>;
}

/**
 * Shown where the sign-in controls would be when clerk-js could not load, so
 * visitors get a message and a way to retry instead of no controls (§16.4).
 */
export function SignInUnavailable() {
  const timedOut = useClerkLoadTimedOut();
  // The live region stays mounted (hidden while empty), so screen readers announce the message.
  return (
    <>
      <span role="status" className={timedOut ? undefined : "visually-hidden"}>
        {timedOut && "Sign-in is unavailable."}
      </span>
      {timedOut && (
        <button type="button" className="button" onClick={reloadPage}>
          Reload
        </button>
      )}
    </>
  );
}
