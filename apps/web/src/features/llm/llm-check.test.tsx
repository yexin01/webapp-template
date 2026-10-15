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
});
