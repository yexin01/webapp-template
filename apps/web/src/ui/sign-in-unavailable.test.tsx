import { act, render, screen } from "@testing-library/react";

import { CLERK_LOAD_BACKSTOP_MS } from "@/lib/use-resource";

import { SignInUnavailable } from "./signed-in-state";

const auth = vi.hoisted(() => ({ isLoaded: false }));
const clerk = vi.hoisted(() => ({ status: "loading" as "loading" | "ready" | "degraded" | "error" }));
vi.mock("@clerk/nextjs", () => ({ useAuth: () => auth, useClerk: () => clerk, Show: () => null }));

beforeEach(() => {
  clerk.status = "loading";
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("sign-in unavailable notice", () => {
  it("tells visitors sign-in is unavailable, with a reload, when Clerk never loads (§16.4)", () => {
    auth.isLoaded = false;
    const reloadPage = vi.fn();
    vi.spyOn(window, "location", "get").mockReturnValue({ ...window.location, reload: reloadPage });
    render(<SignInUnavailable />);
    // The live region is there, empty, from the start: screen readers announce text added to it.
    const region = screen.getByRole("status");
    act(() => vi.advanceTimersByTime(CLERK_LOAD_BACKSTOP_MS - 1));
    expect(region).toBeEmptyDOMElement();
    expect(screen.queryByRole("button", { name: "Reload" })).toBeNull();

    act(() => vi.advanceTimersByTime(1));
    expect(screen.getByRole("status")).toBe(region);
    expect(region).toHaveTextContent("Sign-in is unavailable.");
    act(() => screen.getByRole("button", { name: "Reload" }).click());
    expect(reloadPage).toHaveBeenCalledOnce();
  });

  it("says so at once when the SDK reports that Clerk failed to load", () => {
    auth.isLoaded = false;
    clerk.status = "error";
    const reloadPage = vi.fn();
    vi.spyOn(window, "location", "get").mockReturnValue({ ...window.location, reload: reloadPage });
    render(<SignInUnavailable />);
    expect(screen.getByRole("status")).toHaveTextContent("Sign-in is unavailable.");
    act(() => screen.getByRole("button", { name: "Reload" }).click());
    expect(reloadPage).toHaveBeenCalledOnce();
  });

  it("shows nothing for a degraded Clerk that has loaded", () => {
    auth.isLoaded = true;
    clerk.status = "degraded";
    render(<SignInUnavailable />);
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("shows nothing when Clerk has loaded", () => {
    auth.isLoaded = true;
    render(<SignInUnavailable />);
    act(() => vi.advanceTimersByTime(CLERK_LOAD_BACKSTOP_MS));
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
    expect(screen.queryByRole("button")).toBeNull();
  });
});
