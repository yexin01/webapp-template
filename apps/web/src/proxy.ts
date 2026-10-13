import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";

/**
 * Route protection for page presentation only (FE-05). Every API operation is
 * authorized independently by the Python backend; this never replaces it.
 */
const isProtectedPage = createRouteMatcher(["/dashboard(.*)", "/billing(.*)", "/pro(.*)"]);

export default clerkMiddleware(
  async (auth, request) => {
    if (isProtectedPage(request)) {
      await auth.protect();
    }
  },
  { signInUrl: "/sign-in", signUpUrl: "/sign-up" },
);

export const config = {
  matcher: [
    // Skip Next internals, static files, and the rewritten Python API (/api/v1),
    // whose webhooks need the raw body and whose auth is enforced server-side.
    "/((?!_next|api/v1|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
  ],
};
