import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@/lib/api/errors";

import { ErrorState, LoadingState } from "./state-views";

const navigation = vi.hoisted(() => ({ pathname: "/dashboard" }));
vi.mock("next/navigation", () => ({ usePathname: () => navigation.pathname }));

afterEach(() => {
  navigation.pathname = "/dashboard";
  window.history.replaceState(null, "", "/");
});

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

  it("sends the visitor back to the page they were on, query string included, as a relative path (SEC-14)", () => {
    navigation.pathname = "/billing";
    window.history.replaceState(null, "", "/billing?from=portal");
    render(<ErrorState error={new ApiError(401, "UNAUTHENTICATED", "x", null)} />);
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute(
      "href",
      "/sign-in?redirect_url=%2Fbilling%3Ffrom%3Dportal",
    );
  });

  it.each([
    ["a path that names another origin", "//evil.example.com/dashboard", "/"],
    ["a path with a backslash", "/\\evil.example.com", "/"],
    ["an absolute URL", "https://evil.example.com/", "/"],
  ])("never returns the visitor to %s", (_label, pathname, returnTo) => {
    navigation.pathname = pathname;
    render(<ErrorState error={new ApiError(401, "UNAUTHENTICATED", "x", null)} />);
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute(
      "href",
      `/sign-in?redirect_url=${encodeURIComponent(returnTo)}`,
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

  it("offers the plans, and no retry, for a feature that needs Pro", () => {
    const onRetry = vi.fn();
    render(
      <ErrorState
        error={new ApiError(403, "ENTITLEMENT_REQUIRED", "This feature requires the Pro plan.", null)}
        onRetry={onRetry}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Pro feature");
    expect(screen.getByRole("alert")).toHaveTextContent("This feature requires the Pro plan.");
    expect(screen.getByRole("link", { name: "See plans" })).toHaveAttribute("href", "/billing");
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Sign in" })).toBeNull();
  });

  it("offers nothing to do for a refusal that retrying or signing in cannot change", () => {
    render(<ErrorState error={new ApiError(403, "FORBIDDEN", "No.", null)} onRetry={vi.fn()} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Not allowed");
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });
});
