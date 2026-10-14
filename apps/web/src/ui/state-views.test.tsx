import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@/lib/api/errors";

import { ErrorState, LoadingState } from "./state-views";

vi.mock("next/navigation", () => ({ usePathname: () => "/dashboard" }));

describe("state views", () => {
  it("announces loading politely", () => {
    render(<LoadingState label="Loading projects…" />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading projects…");
  });

  it("offers sign-in with a relative return path for expired sessions", () => {
    render(<ErrorState error={new ApiError(401, "UNAUTHENTICATED", "x", null)} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Your session has ended");
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute(
      "href",
      "/sign-in?redirect_url=%2Fdashboard",
    );
  });

  it("offers retry for transient errors", async () => {
    const onRetry = vi.fn();
    render(
      <ErrorState error={new ApiError(503, "DEPENDENCY_UNAVAILABLE", "Down.", null)} onRetry={onRetry} />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });
});
