import createClient, { type Client } from "openapi-fetch";

import { ApiError, type Problem } from "./errors";
import type { components, paths } from "./schema";

export type Schemas = components["schemas"];
export type TokenGetter = () => Promise<string | null>;

/** The typed client generated from the OpenAPI contract. Paths include the `/api/v1` prefix. */
export type ApiFetchClient = Client<paths>;

type Settled<D> = { data?: D; error?: unknown; response: Response };

/** Every request has a deadline (ARCH-07), so a stalled API cannot leave a screen busy forever. */
export const REQUEST_TIMEOUT_MS = 30_000;
/**
 * Calls the API answers only after reaching an external provider (Stripe,
 * the LLM, integration checks), each bounded by its own server-side timeout.
 */
export const PROVIDER_TIMEOUT_MS = 90_000;

export type ApiClientOptions = { timeoutMs?: number; providerTimeoutMs?: number };

/** No body is allowed for these statuses, so there is nothing to read or validate. */
const NO_BODY = new Set([204, 205, 304]);

function isProblem(value: unknown): value is Problem {
  return typeof value === "object" && value !== null && "code" in value && "status" in value;
}

function isJson(text: string): boolean {
  if (!text.trim()) return false;
  try {
    JSON.parse(text);
    return true;
  } catch {
    return false;
  }
}

/**
 * A signal that aborts when the caller's signal does, or after `ms`. A
 * timeout aborts with a TimeoutError, so it is never mistaken for the
 * caller's own cancellation.
 */
export function withDeadline(signal: AbortSignal, ms: number): { signal: AbortSignal; clear: () => void } {
  const controller = new AbortController();
  const forward = () => controller.abort(signal.reason);
  if (signal.aborted) forward();
  else signal.addEventListener("abort", forward, { once: true });
  const timer = setTimeout(
    () => controller.abort(new DOMException("The request timed out.", "TimeoutError")),
    ms,
  );
  const clear = () => {
    clearTimeout(timer);
    signal.removeEventListener("abort", forward);
  };
  return { signal: controller.signal, clear };
}

/**
 * Sends one request under a deadline and reads its body under the same
 * deadline. Network failures and timeouts become safe errors, a success
 * response whose body is not JSON is an error rather than data, and a
 * caller's abort is re-thrown untouched.
 */
function transport(timeoutMs: number) {
  return async (request: Request): Promise<Response> => {
    const deadline = withDeadline(request.signal, timeoutMs);
    try {
      const response = await globalThis.fetch(request, { signal: deadline.signal });
      if (NO_BODY.has(response.status)) return response;
      const text = await response.text();
      if (response.ok && !isJson(text)) throw ApiError.unexpectedResponse(response.status);
      const { status, statusText, headers } = response;
      return new Response(text, { status, statusText, headers });
    } catch (error) {
      if (error instanceof ApiError) throw error;
      if (deadline.signal.aborted && !request.signal.aborted) throw ApiError.timeout();
      if (error instanceof DOMException && error.name === "AbortError") throw error;
      throw ApiError.network();
    } finally {
      deadline.clear();
    }
  };
}

/** Unwraps a typed result, mapping failures to the error contract (API-02, FE-10). */
async function send<D>(pending: Promise<Settled<D>>): Promise<D> {
  const { data, error, response } = await pending;
  if (response.status === 204) return undefined as D;
  if (response.ok) {
    // The transport admits only JSON success bodies; anything else is not data.
    if (data === undefined) throw ApiError.unexpectedResponse(response.status);
    return data;
  }
  if (isProblem(error)) throw new ApiError(response.status, error.code, error.detail, error.request_id);
  throw ApiError.unexpectedResponse(response.status);
}

/**
 * Typed client for the Python API. Paths, parameters, bodies, and responses
 * are checked at compile time against the generated contract. Authenticates
 * with a short-lived Clerk session token in the Authorization header; cookies
 * are not used for API authentication (API-04).
 */
export function createApiClient(getToken: TokenGetter, options: ApiClientOptions = {}) {
  const { timeoutMs = REQUEST_TIMEOUT_MS, providerTimeoutMs = PROVIDER_TIMEOUT_MS } = options;
  const viaProvider = { fetch: transport(providerTimeoutMs) };
  const client: ApiFetchClient = createClient<paths>({
    // Same-origin: requests go to /api/v1/... on the page's own origin.
    baseUrl: "",
    fetch: transport(timeoutMs),
    headers: { Accept: "application/json" },
    credentials: "omit",
    cache: "no-store",
  });
  client.use({
    async onRequest({ request }) {
      const token = await getToken();
      if (token) request.headers.set("Authorization", `Bearer ${token}`);
      return request;
    },
  });

  const org = (orgId: string) => ({ path: { org_id: orgId } });

  return {
    me: () => send(client.GET("/api/v1/me")),
    entitlement: (orgId: string | null) =>
      send(
        orgId
          ? client.GET("/api/v1/orgs/{org_id}/entitlement", { params: org(orgId) })
          : client.GET("/api/v1/entitlement"),
      ),
    listProjects: (orgId: string | null, cursor?: string | null) => {
      const query = { limit: 20, cursor: cursor || undefined };
      return send(
        orgId
          ? client.GET("/api/v1/orgs/{org_id}/projects", { params: { ...org(orgId), query } })
          : client.GET("/api/v1/projects", { params: { query } }),
      );
    },
    createProject: (orgId: string | null, name: string) =>
      send(
        orgId
          ? client.POST("/api/v1/orgs/{org_id}/projects", { params: org(orgId), body: { name } })
          : client.POST("/api/v1/projects", { body: { name } }),
      ),
    deleteProject: (orgId: string | null, id: string): Promise<void> =>
      send(
        orgId
          ? client.DELETE("/api/v1/orgs/{org_id}/projects/{project_id}", {
              params: { path: { org_id: orgId, project_id: id } },
            })
          : client.DELETE("/api/v1/projects/{project_id}", { params: { path: { project_id: id } } }),
      ),
    billingStatus: () => send(client.GET("/api/v1/billing/status")),
    startCheckout: (plan: Schemas["CheckoutRequest"]["plan"], idempotencyKey: string) =>
      send(
        client.POST("/api/v1/billing/checkout", {
          params: { header: { "Idempotency-Key": idempotencyKey } },
          body: { plan },
          ...viaProvider,
        }),
      ),
    openPortal: () => send(client.POST("/api/v1/billing/portal", viaProvider)),
    proInsight: (orgId: string | null) =>
      send(
        orgId
          ? client.GET("/api/v1/orgs/{org_id}/pro/insight", { params: org(orgId) })
          : client.GET("/api/v1/pro/insight"),
      ),
    llmCheck: () => send(client.POST("/api/v1/llm/check", viaProvider)),
    integrations: () => send(client.GET("/api/v1/diagnostics/integrations")),
    runIntegrationCheck: (key: Schemas["IntegrationKey"]) =>
      send(
        client.POST("/api/v1/diagnostics/integrations/{key}/check", {
          params: { path: { key } },
          ...viaProvider,
        }),
      ),
    // new:module:client (pnpm new:module adds methods above this line)
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
