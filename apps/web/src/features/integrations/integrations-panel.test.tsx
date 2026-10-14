import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@/lib/api/errors";
import type { Schemas } from "@/lib/api/client";

import { IntegrationsPanel } from "./integrations-panel";

const api = {
  integrations: vi.fn<() => Promise<Schemas["IntegrationsReport"]>>(),
  runIntegrationCheck: vi.fn<(key: Schemas["IntegrationKey"]) => Promise<Schemas["IntegrationCheck"]>>(),
};

vi.mock("@/lib/api/use-api", () => ({ useApi: () => api }));
vi.mock("next/navigation", () => ({ usePathname: () => "/integrations" }));
vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ isLoaded: true, isSignedIn: true, sessionId: "sess_1" }),
  useOrganization: () => ({ organization: null }),
}));
vi.mock("@sentry/nextjs", () => ({ getClient: () => undefined, captureException: vi.fn(), flush: vi.fn() }));

const report: Schemas["IntegrationsReport"] = {
  environment: "local",
  integrations: [
    { key: "database", name: "PostgreSQL", mode: "local", details: ["Local server"] },
    { key: "billing", name: "Stripe", mode: "sandbox", details: ["Pro prices configured: monthly, yearly"] },
    { key: "llm", name: "LLM provider", mode: "mock", details: ["Mock provider"] },
  ],
};

function result(key: Schemas["IntegrationKey"], patch: Partial<Schemas["IntegrationCheck"]> = {}) {
  return {
    key,
    mode: "sandbox",
    outcome: "passed",
    summary: `${key} ok`,
    latency_ms: 12,
    error_code: null,
    reference: null,
    ...patch,
  } satisfies Schemas["IntegrationCheck"];
}

beforeEach(() => {
  api.integrations.mockReset().mockResolvedValue(report);
  api.runIntegrationCheck.mockReset().mockImplementation(async (key) => result(key));
});

function card(name: string) {
  return screen.getByRole("heading", { name, level: 3 }).closest("li") as HTMLElement;
}

describe("integrations panel", () => {
  it("lists every integration with its mode and details", async () => {
    render(<IntegrationsPanel />);
    expect(await screen.findByRole("heading", { name: "Stripe", level: 3 })).toBeInTheDocument();
    expect(within(card("Stripe")).getByText("Sandbox")).toBeInTheDocument();
    expect(within(card("LLM provider")).getByText("Mock")).toBeInTheDocument();
    expect(within(card("PostgreSQL")).getByText("Local server")).toBeInTheDocument();
    expect(screen.getByText("Environment: local")).toBeInTheDocument();
  });

  it("runs one check and announces the result", async () => {
    render(<IntegrationsPanel />);
    await userEvent.click(await screen.findByRole("button", { name: "Run check for Stripe" }));
    expect(api.runIntegrationCheck).toHaveBeenCalledWith("billing");
    const stripe = within(card("Stripe"));
    expect(stripe.getByText("Passed")).toBeInTheDocument();
    expect(stripe.getByText("billing ok")).toBeInTheDocument();
  });

  it("labels skipped and unconfirmed outcomes honestly", async () => {
    api.runIntegrationCheck.mockImplementation(async (key) =>
      key === "llm"
        ? result(key, { mode: "mock", outcome: "skipped", summary: "Mock mode" })
        : result(key, { outcome: "sent", reference: "evt123" }),
    );
    render(<IntegrationsPanel />);
    await userEvent.click(await screen.findByRole("button", { name: "Run all server checks" }));
    expect(await within(card("LLM provider")).findByText("Skipped")).toBeInTheDocument();
    expect(within(card("Stripe")).getByText("Sent (unconfirmed)")).toBeInTheDocument();
    expect(within(card("Stripe")).getByText("evt123")).toBeInTheDocument();
    expect(api.runIntegrationCheck.mock.calls.map(([key]) => key)).toEqual(["database", "billing", "llm"]);
  });

  it("shows a per-card error without hiding other results", async () => {
    api.runIntegrationCheck.mockRejectedValueOnce(new ApiError(429, "RATE_LIMITED", "Too many.", null));
    render(<IntegrationsPanel />);
    await userEvent.click(await screen.findByRole("button", { name: "Run check for PostgreSQL" }));
    expect(within(card("PostgreSQL")).getByRole("alert")).toHaveTextContent("Slow down");
  });

  it("explains when diagnostics are unavailable", async () => {
    api.integrations.mockRejectedValue(new ApiError(404, "NOT_FOUND", "Not found.", null));
    render(<IntegrationsPanel />);
    expect(await screen.findByText(/Diagnostics are not available/)).toBeInTheDocument();
  });

  it("reports disabled browser telemetry as skipped", async () => {
    render(<IntegrationsPanel />);
    await userEvent.click(await screen.findByRole("button", { name: "Send test event" }));
    expect(within(card("PostHog (browser)")).getByText("Skipped")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Send test error" }));
    expect(await within(card("Sentry (browser)")).findByText("Skipped")).toBeInTheDocument();
  });
});
