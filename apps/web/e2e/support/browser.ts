import { expect, test as base } from "@playwright/test";

import { isSandboxRun } from "./sandbox";

export { expect };

/**
 * Browser tests run in two tiers, chosen by E2E_SANDBOX:
 *
 * - Account-free (the default, and what CI runs): the app is built with the placeholder Clerk keys of
 *   .env.example, which no Clerk instance can match. A development key sends every first page request to
 *   Clerk's Frontend API for a browser handshake, and the placeholder host does not exist. Clerk skips the
 *   handshake when the request already carries a development browser token, so each test's browser starts
 *   with one (an arbitrary value: with no session cookies the server only checks that one is present), and
 *   every request that is not to the app itself is aborted, so nothing leaves the machine. clerk-js cannot
 *   load, which is itself a state the app must handle ("Sign-in is unavailable"). The Clerk-drawn controls
 *   (sign-in form, user menu) never render, so they are covered only by the sandbox tier. Mocked evidence.
 * - Sandbox: real Clerk development keys; nothing is faked here.
 *
 * Whichever tier runs, the page must raise no uncaught error (clerk-js failing to load excepted in the
 * account-free tier), report no Content Security Policy violation, and request nothing of the app's own
 * that fails: a CSP that blocks the app's scripts or a broken asset fails these tests, not a user. Only
 * the app's own pages count: a page the browser is sent to (Stripe's Checkout) has policies and scripts of
 * its own. A test that provokes a failing response on purpose names it in `expectedFailures`.
 */
export const ACCOUNT_FREE = !isSandboxRun(process.env);

const DEV_BROWSER_COOKIE = "__clerk_db_jwt";
// What clerk-js's loader throws when its script is blocked (account-free runs block it on purpose).
const CLERK_JS_BLOCKED = /failed_to_load_clerk_js/;
// Chromium reports this benign layout notification as an uncaught error.
const BENIGN = /ResizeObserver loop/;

type CspWindow = { reportCspViolation?: (detail: string) => void };

export const test = base.extend<{ guard: void; expectedFailures: string[] }>({
  // Same-origin responses a test provokes on purpose, as "<status> <path>"; the test adds them before it navigates.
  expectedFailures: [
    async ({}, use) => {
      await use([]);
    },
    { scope: "test" },
  ],
  guard: [
    async ({ page, context, baseURL, expectedFailures }, use) => {
      const origin = new URL(baseURL ?? "http://127.0.0.1");
      const problems: string[] = [];

      if (ACCOUNT_FREE) {
        await context.addCookies([
          { name: DEV_BROWSER_COOKIE, value: "dvb_e2e_placeholder", url: origin.origin },
        ]);
        await context.route(
          (url) => /^https?:$/.test(url.protocol) && url.hostname !== origin.hostname,
          (route) => route.abort(),
        );
        // The app never navigates off-site by itself. Clerk does, to its Frontend API, when the development
        // handshake is not skipped (a Clerk upgrade that renamed its cookie, for example). The hop after a
        // redirect fails at the host lookup before the route above sees it, so it is the request event
        // that tells: say so, instead of failing on a bare ERR_NAME_NOT_RESOLVED.
        page.on("request", (request) => {
          const { hostname } = new URL(request.url());
          if (request.isNavigationRequest() && hostname !== origin.hostname) {
            problems.push(
              `the browser was sent to ${hostname}: Clerk's development handshake was not skipped (is the dev-browser cookie still named ${DEV_BROWSER_COOKIE}?)`,
            );
          }
        });
      }

      // A binding, not a variable in the page, so violations before a navigation or a reload are kept too.
      await page.exposeFunction("reportCspViolation", (detail: string) => {
        problems.push(`Content Security Policy violation: ${detail}`);
      });
      await page.addInitScript((appOrigin) => {
        document.addEventListener("securitypolicyviolation", (event) => {
          // Our policy is reported on our own documents; a page the browser was sent to reports its own.
          if (window.location.origin !== appOrigin) return;
          (window as unknown as CspWindow).reportCspViolation?.(
            `${event.violatedDirective} blocked ${event.blockedURI || "an inline resource"}`,
          );
        });
      }, origin.origin);
      page.on("pageerror", (error) => {
        if (BENIGN.test(error.message) || (ACCOUNT_FREE && CLERK_JS_BLOCKED.test(error.message))) return;
        if (new URL(page.url()).origin !== origin.origin) return;
        problems.push(`uncaught error: ${error.message}`);
      });
      page.on("response", (response) => {
        const { hostname, pathname } = new URL(response.url());
        if (hostname !== origin.hostname || response.status() < 400) return;
        // The document under test may be a 404 on purpose; the browser asks for the icon on its own.
        if (response.request().resourceType() === "document" || pathname === "/favicon.ico") return;
        if (expectedFailures.includes(`${response.status()} ${pathname}`)) return;
        problems.push(`${response.status()} for ${pathname}`);
      });

      await use();

      expect(problems, "the page misbehaved").toEqual([]);
    },
    { auto: true },
  ],
});
