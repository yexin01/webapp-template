import createClient, { type Client } from "openapi-fetch";

import { ApiError, type Problem } from "./errors";
import type { components, paths } from "./schema";

export type Schemas = components["schemas"];
export type TokenGetter = () => Promise<string | null>;

/** The typed client generated from the OpenAPI contract. Paths include the `/api/v1` prefix. */
export type ApiFetchClient = Client<paths>;

type Settled<D> = { data?: D; error?: unknown; response: Response };

function isProblem(value: unknown): value is Problem {
  return typeof value === "object" && value !== null && "code" in value && "status" in value;
}

/** Network failures become one safe error; an aborted request is re-thrown untouched. */
async function transport(request: Request): Promise<Response> {
  try {
    return await globalThis.fetch(request);
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw ApiError.network();
  }
}

/** Unwraps a typed result, mapping failures to the error contract (API-02, FE-10). */
async function send<D>(pending: Promise<Settled<D>>): Promise<D> {
  let settled: Settled<D>;
  try {
    settled = await pending;
  } catch (error) {
    // A success response whose body is not JSON resolves to null, as before.
    if (error instanceof SyntaxError) return null as D;
    throw error;
  }
  const { data, error, response } = settled;
  if (response.status === 204) return undefined as D;
  if (response.ok) return (data ?? null) as D;
  if (isProblem(error)) throw new ApiError(response.status, error.code, error.detail, error.request_id);
  throw new ApiError(response.status, "HTTP_ERROR", "The server returned an unexpected response.", null);
}

/**
 * Typed client for the Python API. Paths, parameters, bodies, and responses
 * are checked at compile time against the generated contract. Authenticates
 * with a short-lived Clerk session token in the Authorization header; cookies
 * are not used for API authentication (API-04).
 */
export function createApiClient(getToken: TokenGetter) {
  const client: ApiFetchClient = createClient<paths>({
    // Same-origin: requests go to /api/v1/... on the page's own origin.
    baseUrl: "",
    fetch: transport,
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
        }),
      ),
    openPortal: () => send(client.POST("/api/v1/billing/portal")),
    proInsight: (orgId: string | null) =>
      send(
        orgId
          ? client.GET("/api/v1/orgs/{org_id}/pro/insight", { params: org(orgId) })
          : client.GET("/api/v1/pro/insight"),
      ),
    llmCheck: () => send(client.POST("/api/v1/llm/check")),
    integrations: () => send(client.GET("/api/v1/diagnostics/integrations")),
    runIntegrationCheck: (key: Schemas["IntegrationKey"]) =>
      send(client.POST("/api/v1/diagnostics/integrations/{key}/check", { params: { path: { key } } })),
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
