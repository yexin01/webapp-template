// @vitest-environment node
import { readFileSync } from "node:fs";
import path from "node:path";

import { ApiError, presentError, type ErrorPresentation } from "./errors";

/**
 * FE-10: one mapping from API errors to what the user reads and the action offered. Every code the mapping
 * names is listed here with its exact copy, so a changed title, a changed recovery action, or a server
 * message shown where it must not be, fails by name. client.test.ts covers the same mapping through the client.
 */
const SERVER = "A message the server wrote for the user.";
const GENERIC: ErrorPresentation = {
  title: "Something went wrong",
  message: "An unexpected error occurred.",
  action: "retry",
};
const UNAVAILABLE: ErrorPresentation = { title: "Temporarily unavailable", message: SERVER, action: "retry" };
const REFUSED: ErrorPresentation = { title: "Could not complete this", message: SERVER, action: "none" };

/** [status, code, what the user is shown]. A message of SERVER is the server's own text, shown as it is. */
const TABLE: [number, string, ErrorPresentation][] = [
  [422, "VALIDATION_FAILED", { title: "Check your input", message: SERVER, action: "none" }],
  [409, "CONFLICT", { title: "Could not complete this", message: SERVER, action: "retry" }],
  [400, "INVALID_REQUEST", { title: "Request not accepted", message: SERVER, action: "retry" }],
  [
    401,
    "UNAUTHENTICATED",
    { title: "Your session has ended", message: "Sign in again to continue.", action: "sign-in" },
  ],
  [0, "AUTH_UNAVAILABLE", { title: "Sign-in unavailable", message: SERVER, action: "retry" }],
  [403, "ENTITLEMENT_REQUIRED", { title: "Pro feature", message: SERVER, action: "upgrade" }],
  [403, "FORBIDDEN", { title: "Not allowed", message: SERVER, action: "none" }],
  [
    404,
    "NOT_FOUND",
    {
      title: "Not found",
      message: "This item does not exist or you do not have access to it.",
      action: "none",
    },
  ],
  [
    429,
    "RATE_LIMITED",
    { title: "Slow down", message: "Too many requests. Wait a moment and try again.", action: "retry" },
  ],
  // A shared budget, not the user's own pace: retrying soon cannot help.
  [429, "BUDGET_EXCEEDED", { title: "Limit reached", message: SERVER, action: "none" }],
  [503, "DEPENDENCY_UNAVAILABLE", UNAVAILABLE],
  [503, "NOT_CONFIGURED", UNAVAILABLE],
  [0, "NETWORK_ERROR", UNAVAILABLE],
  [0, "TIMEOUT", UNAVAILABLE],
  // A failure of ours, or a response outside the contract: its text is never shown.
  [500, "INTERNAL_ERROR", GENERIC],
  [502, "HTTP_ERROR", GENERIC],
];

describe("presentError", () => {
  it.each(TABLE)("presents %i %s", (status, code, expected) => {
    expect(
      presentError(new ApiError(status, code, SERVER, "req-1")),
      `${code}: the title, the copy, or the recovery action changed`,
    ).toEqual(expected);
  });

  it("lists every code the mapping names, so a new case cannot go untested", () => {
    const source = readFileSync(path.join(import.meta.dirname, "errors.ts"), "utf8");
    const named = [...source.matchAll(/case "([A-Z_]+)":/g)].map((match) => match[1]);
    expect(named.length).toBeGreaterThan(0);
    expect(TABLE.map(([, code]) => code)).toEqual(expect.arrayContaining(named));
  });

  it.each([
    [399, GENERIC],
    [400, REFUSED],
    [404, REFUSED],
    [499, REFUSED],
    [500, GENERIC],
    [502, GENERIC],
    [503, UNAVAILABLE],
    [504, GENERIC],
  ])("presents a code a module added, with status %i", (status, expected) => {
    expect(presentError(new ApiError(status, "A_MODULE_CODE", SERVER, null))).toEqual(expected);
  });

  it("never shows the text of anything that is not an API error", () => {
    const detail = "Database exploded at db.internal:5432";
    for (const error of [
      new Error(detail),
      detail,
      null,
      undefined,
      { code: "NOT_FOUND", message: detail },
    ]) {
      expect(presentError(error)).toEqual(GENERIC);
    }
  });

  it("shows the copy of the errors the client raises itself", () => {
    expect(presentError(ApiError.network())).toEqual({
      title: "Temporarily unavailable",
      message: "Could not reach the server. Check your connection and try again.",
      action: "retry",
    });
    expect(presentError(ApiError.timeout())).toEqual({
      title: "Temporarily unavailable",
      message: "The server took too long to respond. Try again.",
      action: "retry",
    });
    expect(presentError(ApiError.authUnavailable())).toEqual({
      title: "Sign-in unavailable",
      message: "Sign-in could not be loaded. Check your connection, then reload the page.",
      action: "retry",
    });
    expect(presentError(ApiError.unexpectedResponse(200))).toEqual(GENERIC);
  });
});
