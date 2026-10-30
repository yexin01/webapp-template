import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@/lib/api/errors";

import { LlmCheck } from "./llm-check";

const api = vi.hoisted(() => ({ llmCheck: vi.fn() }));
vi.mock("@/lib/api/use-api", () => ({ useApi: () => api }));
vi.mock("next/navigation", () => ({ usePathname: () => "/dashboard" }));

describe("LLM check", () => {
  it("never shows an earlier result next to a new check's error", async () => {
    api.llmCheck
      .mockResolvedValueOnce({ ok: true, provider: "mock", model: "mock-1", latency_ms: 3, error_code: null })
      .mockRejectedValueOnce(new ApiError(503, "DEPENDENCY_UNAVAILABLE", "The provider is down.", null));
    render(<LlmCheck />);
    await userEvent.click(screen.getByRole("button", { name: "Run check" }));
    expect(await screen.findByText("Connected to mock (mock-1) in 3 ms.")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Run check" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The provider is down.");
    expect(screen.queryByText(/Connected to mock/)).toBeNull();
  });

  it("explains that the check is disabled instead of reporting a failure", async () => {
    api.llmCheck.mockResolvedValueOnce({
      ok: false,
      provider: "none",
      model: "",
      latency_ms: 0,
      error_code: "not_configured",
    });
    render(<LlmCheck />);
    await userEvent.click(screen.getByRole("button", { name: "Run check" }));
    expect(await screen.findByText(/LLM checks are turned off in this deployment/)).toBeInTheDocument();
    expect(screen.getByText(/LLM_MODE=disabled/)).toBeInTheDocument();
    expect(screen.queryByText(/Check failed/)).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("still reports a provider's failure", async () => {
    api.llmCheck.mockResolvedValueOnce({
      ok: false,
      provider: "anthropic",
      model: "m",
      latency_ms: 4,
      error_code: "auth_failed",
    });
    render(<LlmCheck />);
    await userEvent.click(screen.getByRole("button", { name: "Run check" }));
    expect(await screen.findByText("Check failed for anthropic: auth_failed.")).toBeInTheDocument();
    expect(screen.queryByText(/turned off/)).toBeNull();
  });
});
