import type { components } from "./schema";

export type Problem = components["schemas"]["Problem"];

/** A normalized API failure (API-02). Never contains submitted values. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;

  constructor(status: number, code: string, detail: string, requestId: string | null) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }

  static network(): ApiError {
    return new ApiError(
      0,
      "NETWORK_ERROR",
      "Could not reach the server. Check your connection and try again.",
      null,
    );
  }

  static timeout(): ApiError {
    return new ApiError(0, "TIMEOUT", "The server took too long to respond. Try again.", null);
  }

  /** A response outside the contract: a success without a JSON body, or an error that is not a problem. */
  static unexpectedResponse(status: number): ApiError {
    return new ApiError(status, "HTTP_ERROR", "The server returned an unexpected response.", null);
  }

  /** clerk-js never loaded (a blocked script, or its CDN or Frontend API is down), so no session exists. */
  static authUnavailable(): ApiError {
    return new ApiError(
      0,
      "AUTH_UNAVAILABLE",
      "Sign-in could not be loaded. Check your connection, then reload the page.",
      null,
    );
  }
}

const INPUT_ERRORS = new Set(["VALIDATION_FAILED", "INVALID_REQUEST", "CONFLICT"]);

/**
 * For a form submission: true when the server rejected what the user entered,
 * so the message belongs next to the field. Outside a form, the same codes
 * describe the request or the current state, and presentError titles them so.
 */
export function isInputError(error: unknown): boolean {
  return error instanceof ApiError && INPUT_ERRORS.has(error.code);
}

export type ErrorPresentation = {
  title: string;
  message: string;
  action: "sign-in" | "retry" | "upgrade" | "none";
};

const GENERIC: ErrorPresentation = {
  title: "Something went wrong",
  message: "An unexpected error occurred.",
  action: "retry",
};

/** One mapping from API errors to user-facing copy, used by every screen (FE-10). */
export function presentError(error: unknown): ErrorPresentation {
  if (!(error instanceof ApiError)) return GENERIC;
  switch (error.code) {
    case "VALIDATION_FAILED":
      return { title: "Check your input", message: error.message, action: "none" };
    case "CONFLICT":
      // The current state does not allow it, for example Pro is already active (BILL-07).
      return { title: "Could not complete this", message: error.message, action: "retry" };
    case "INVALID_REQUEST":
      return { title: "Request not accepted", message: error.message, action: "retry" };
    case "UNAUTHENTICATED":
      return { title: "Your session has ended", message: "Sign in again to continue.", action: "sign-in" };
    case "AUTH_UNAVAILABLE":
      return { title: "Sign-in unavailable", message: error.message, action: "retry" };
    case "ENTITLEMENT_REQUIRED":
      return { title: "Pro feature", message: error.message, action: "upgrade" };
    case "FORBIDDEN":
      return { title: "Not allowed", message: error.message, action: "none" };
    case "NOT_FOUND":
      return {
        title: "Not found",
        message: "This item does not exist or you do not have access to it.",
        action: "none",
      };
    case "RATE_LIMITED":
      return {
        title: "Slow down",
        message: "Too many requests. Wait a moment and try again.",
        action: "retry",
      };
    case "BUDGET_EXCEEDED":
      // A shared budget, not the user's own pace: retrying soon cannot help.
      return { title: "Limit reached", message: error.message, action: "none" };
    case "DEPENDENCY_UNAVAILABLE":
    case "NOT_CONFIGURED":
    case "NETWORK_ERROR":
    case "TIMEOUT":
      return { title: "Temporarily unavailable", message: error.message, action: "retry" };
    case "HTTP_ERROR":
      // A response outside the contract (a proxy's page): its text is never shown.
      return GENERIC;
    default:
      // A module may add error codes with a 4xx or 503 status; their messages are written for users.
      if (error.status >= 400 && error.status < 500) {
        return { title: "Could not complete this", message: error.message, action: "none" };
      }
      if (error.status === 503)
        return { title: "Temporarily unavailable", message: error.message, action: "retry" };
      return GENERIC;
  }
}
