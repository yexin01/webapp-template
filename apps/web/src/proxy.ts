import { clerkMiddleware } from "@clerk/nextjs/server";

import { isPublicPage } from "@/lib/page-access";

// The origins a session token may have been issued for (its azp claim), derived by next.config.ts from
// CLERK_AUTHORIZED_PARTIES and APP_BASE_URL with the API's rules. Empty only under APP_ENV=test.
const authorizedParties = (process.env.NEXT_PUBLIC_CLERK_AUTHORIZED_PARTIES ?? "").split(",").filter(Boolean);

/**
 * Page access for presentation only (FE-05): a signed-out visitor is sent to
 * sign-in from every page except the ones listed in lib/page-access.ts, so a
 * new page requires sign-in by default. Every API operation is authorized
 * independently by the Python backend; this never replaces it. The browser
 * half is the page gate (ui/page-gate.tsx), for a deployment where this does
 * not run.
 */
export default clerkMiddleware(
  async (auth, request) => {
    if (!isPublicPage(request.nextUrl.pathname)) {
      await auth.protect();
    }
  },
  {
    signInUrl: "/sign-in",
    signUpUrl: "/sign-up",
    authorizedParties: authorizedParties.length > 0 ? authorizedParties : undefined,
  },
);

export const config = {
  matcher: [
    // Skip Next internals, static files, and the rewritten Python API (/api/v1),
    // whose webhooks need the raw body and whose auth is enforced server-side.
    "/((?!_next|api/v1|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest|txt|xml)).*)",
    // Clerk's Frontend API proxy and the clerk-js files it serves end in .js, which the pattern above skips.
    "/__clerk/(.*)",
  ],
};
