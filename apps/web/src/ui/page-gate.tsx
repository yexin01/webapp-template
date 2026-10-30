"use client";

import { useAuth } from "@clerk/nextjs";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { ApiError } from "@/lib/api/errors";
import { isPublicPage, signInUrlFor } from "@/lib/page-access";
import { reloadPage, useClerkUnavailable } from "@/lib/use-resource";

import { ErrorState, LoadingState } from "./state-views";

/**
 * The browser half of default-deny page access (FE-05), mounted once around
 * every page by the root layout. The proxy (src/proxy.ts) redirects a
 * signed-out visitor on the server, but a deployment may not run it (Vercel's
 * Build Output API says middleware is not supported inside a service), so a
 * page that is not on the allowlist (lib/page-access.ts, the same one the
 * proxy reads) renders nothing of its own until Clerk reports a signed-in
 * user, and sends a signed-out visitor to sign-in with a relative return path
 * (SEC-14). Clerk has not loaded while a page is prerendered, so the static
 * HTML of a protected page holds only the placeholder, and pages stay static.
 *
 * This is presentation only: the API authorizes every operation itself.
 */
export function PageGate({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  if (isPublicPage(pathname)) return <>{children}</>;
  // Keyed by path: one page being shown never lets the next one through.
  return <ProtectedPage key={pathname}>{children}</ProtectedPage>;
}

function ProtectedPage({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { isLoaded, isSignedIn } = useAuth();
  const unavailable = useClerkUnavailable();
  const signedIn = isLoaded && isSignedIn === true;
  // The gate decides who may see the page; it does not take the page away from someone who has it. A
  // session that ends meanwhile surfaces as an API 401 whose sign-in action keeps form state (FE-04),
  // Clerk's own sign-out navigation is not raced, and a transient Clerk state does not unmount the page.
  const [shown, setShown] = useState(false);
  if (signedIn && !shown) setShown(true);
  const denied = isLoaded && !signedIn && !shown;

  useEffect(() => {
    if (denied) router.replace(signInUrlFor(pathname, window.location.search));
  }, [denied, pathname, router]);

  if (signedIn || shown) return <>{children}</>;
  if (unavailable) return <ErrorState error={ApiError.authUnavailable()} onRetry={reloadPage} />;
  return <LoadingState label={denied ? "Redirecting to sign in…" : "Loading…"} />;
}
