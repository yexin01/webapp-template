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
}

const INPUT_ERRORS = new Set(["VALIDATION_FAILED", "INVALID_REQUEST", "CONFLICT"]);

/** True when the server rejected what the user entered, so the message belongs next to the field. */
export function isInputError(error: unknown): boolean {
  return error instanceof ApiError && INPUT_ERRORS.has(error.code);
}

export type ErrorPresentation = {
  title: string;
  message: string;
  action: "sign-in" | "retry" | "upgrade" | "none";
};

/** One mapping from API errors to user-facing copy, used by every screen (FE-10). */
export function presentError(error: unknown): ErrorPresentation {
  if (!(error instanceof ApiError)) {
    return { title: "Something went wrong", message: "An unexpected error occurred.", action: "retry" };
  }
  if (isInputError(error)) return { title: "Check your input", message: error.message, action: "none" };
  switch (error.code) {
    case "UNAUTHENTICATED":
      return { title: "Your session has ended", message: "Sign in again to continue.", action: "sign-in" };
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
    case "DEPENDENCY_UNAVAILABLE":
    case "NOT_CONFIGURED":
    case "NETWORK_ERROR":
    case "TIMEOUT":
      return { title: "Temporarily unavailable", message: error.message, action: "retry" };
    default:
      return { title: "Something went wrong", message: "An unexpected error occurred.", action: "retry" };
  }
}
