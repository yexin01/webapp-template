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

  it("shows the API's request ID as a reference for a server error, never for a rejected request", () => {
    const { rerender } = render(
      <ErrorState error={new ApiError(500, "INTERNAL_ERROR", "Oops.", "0f1e2d3c4b5a69788796a5b4c3d2e1f0")} />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Reference: 0f1e2d3c4b5a69788796a5b4c3d2e1f0");

    rerender(
      <ErrorState
        error={new ApiError(503, "DEPENDENCY_UNAVAILABLE", "Down.", "11112222333344445555666677778888")}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Reference: 11112222333344445555666677778888");

    rerender(
      <ErrorState error={new ApiError(403, "FORBIDDEN", "No.", "aaaabbbbccccddddeeeeffff00001111")} />,
    );
    expect(screen.getByRole("alert")).not.toHaveTextContent("Reference");
    rerender(<ErrorState error={new ApiError(502, "HTTP_ERROR", "Bad gateway.", null)} />);
    expect(screen.getByRole("alert")).not.toHaveTextContent("Reference");
    rerender(<ErrorState error={new Error("not an API error")} />);
    expect(screen.getByRole("alert")).not.toHaveTextContent("Reference");
  });
});
