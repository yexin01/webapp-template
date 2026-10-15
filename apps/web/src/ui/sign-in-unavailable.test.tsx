import { act, render, screen } from "@testing-library/react";

import { REQUEST_TIMEOUT_MS } from "@/lib/api/client";

import { SignInUnavailable } from "./signed-in-state";

const auth = vi.hoisted(() => ({ isLoaded: false }));
vi.mock("@clerk/nextjs", () => ({ useAuth: () => auth, Show: () => null }));

beforeEach(() => vi.useFakeTimers());
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
    act(() => vi.advanceTimersByTime(REQUEST_TIMEOUT_MS - 1));
    expect(region).toBeEmptyDOMElement();
    expect(screen.queryByRole("button", { name: "Reload" })).toBeNull();

    act(() => vi.advanceTimersByTime(1));
    expect(screen.getByRole("status")).toBe(region);
    expect(region).toHaveTextContent("Sign-in is unavailable.");
    act(() => screen.getByRole("button", { name: "Reload" }).click());
    expect(reloadPage).toHaveBeenCalledOnce();
  });

  it("shows nothing when Clerk has loaded", () => {
    auth.isLoaded = true;
    render(<SignInUnavailable />);
    act(() => vi.advanceTimersByTime(REQUEST_TIMEOUT_MS));
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
    expect(screen.queryByRole("button")).toBeNull();
  });
});
