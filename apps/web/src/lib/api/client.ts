import { ApiError, type Problem } from "./errors";
import type { components } from "./schema";

export type Schemas = components["schemas"];
export type TokenGetter = () => Promise<string | null>;

type RequestOptions = {
  method?: "GET" | "POST" | "DELETE";
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
};

const BASE = "/api/v1";

function isProblem(value: unknown): value is Problem {
  return typeof value === "object" && value !== null && "code" in value && "status" in value;
}

/**
 * Typed client for the Python API. Authenticates with a short-lived Clerk
 * session token in the Authorization header; cookies are not used for API
 * authentication (API-04).
 */
export function createApiClient(getToken: TokenGetter) {
  async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const token = await getToken();
    const headers: Record<string, string> = { Accept: "application/json", ...options.headers };
    if (token) headers.Authorization = `Bearer ${token}`;
    if (options.body !== undefined) headers["Content-Type"] = "application/json";

    let response: Response;
    try {
      response = await fetch(`${BASE}${path}`, {
        method: options.method ?? "GET",
        headers,
        body: options.body === undefined ? null : JSON.stringify(options.body),
        signal: options.signal ?? null,
        credentials: "omit",
        cache: "no-store",
      });
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") throw error;
      throw ApiError.network();
    }

    if (response.status === 204) return undefined as T;
    const payload: unknown = await response.json().catch(() => null);
    if (!response.ok) {
      if (isProblem(payload)) {
        throw new ApiError(response.status, payload.code, payload.detail, payload.request_id);
      }
      throw new ApiError(response.status, "HTTP_ERROR", "The server returned an unexpected response.", null);
    }
    return payload as T;
  }

  const scope = (orgId: string | null) => (orgId ? `/orgs/${encodeURIComponent(orgId)}` : "");

  return {
    me: () => request<Schemas["Me"]>("/me"),
    entitlement: (orgId: string | null) => request<Schemas["Entitlement"]>(`${scope(orgId)}/entitlement`),
    listProjects: (orgId: string | null, cursor?: string | null) =>
      request<Schemas["ProjectPage"]>(
        `${scope(orgId)}/projects?limit=20${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`,
      ),
    createProject: (orgId: string | null, name: string) =>
      request<Schemas["Project"]>(`${scope(orgId)}/projects`, { method: "POST", body: { name } }),
    deleteProject: (orgId: string | null, id: string) =>
      request<void>(`${scope(orgId)}/projects/${encodeURIComponent(id)}`, { method: "DELETE" }),
    billingStatus: () => request<Schemas["BillingStatus"]>("/billing/status"),
    startCheckout: (plan: Schemas["CheckoutRequest"]["plan"], idempotencyKey: string) =>
      request<Schemas["Redirect"]>("/billing/checkout", {
        method: "POST",
        body: { plan },
        headers: { "Idempotency-Key": idempotencyKey },
      }),
    openPortal: () => request<Schemas["Redirect"]>("/billing/portal", { method: "POST" }),
    proInsight: (orgId: string | null) => request<Schemas["ProInsight"]>(`${scope(orgId)}/pro/insight`),
    llmCheck: () => request<Schemas["LLMCheck"]>("/llm/check", { method: "POST" }),
    integrations: () => request<Schemas["IntegrationsReport"]>("/diagnostics/integrations"),
    runIntegrationCheck: (key: Schemas["IntegrationKey"]) =>
      request<Schemas["IntegrationCheck"]>(`/diagnostics/integrations/${encodeURIComponent(key)}/check`, {
        method: "POST",
      }),
  };
}

export type ApiClient = ReturnType<typeof createApiClient>;

/** Only same-origin or Stripe-hosted redirects are followed (SEC-14). */
export function isAllowedRedirect(url: string, currentOrigin: string): boolean {
  try {
    const target = new URL(url, currentOrigin);
    if (target.origin === currentOrigin) return true;
    return target.protocol === "https:" && /(^|\.)stripe\.com$/.test(target.hostname);
  } catch {
    return false;
  }
}
