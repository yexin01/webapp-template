import { act, render, screen } from "@testing-library/react";

import { REQUEST_TIMEOUT_MS } from "@/lib/api/client";

import { ProFeature } from "./pro-feature";

const api = vi.hoisted(() => ({ proInsight: vi.fn() }));
const auth = vi.hoisted(() => ({ isLoaded: true, userId: "user_1" as string | undefined }));
vi.mock("@/lib/api/use-api", () => ({ useApi: () => api }));
vi.mock("@/lib/analytics", () => ({ track: vi.fn() }));
vi.mock("next/navigation", () => ({ usePathname: () => "/pro" }));
vi.mock("@clerk/nextjs", () => ({
  useAuth: () => auth,
  useOrganization: () => ({ isLoaded: auth.isLoaded, organization: null }),
}));

beforeEach(() => {
  vi.useFakeTimers();
  api.proInsight.mockReset();
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("Pro feature", () => {
  it("offers a reload instead of loading forever when Clerk never loads (§16.4)", () => {
    auth.isLoaded = false;
    auth.userId = undefined;
    const reloadPage = vi.fn();
    vi.spyOn(window, "location", "get").mockReturnValue({ ...window.location, reload: reloadPage });
    render(<ProFeature />);
    expect(screen.getByRole("status")).toBeInTheDocument();

    act(() => vi.advanceTimersByTime(REQUEST_TIMEOUT_MS));
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Sign-in unavailable");
    act(() => screen.getByRole("button", { name: "Try again" }).click());
    expect(reloadPage).toHaveBeenCalledOnce();
    expect(api.proInsight).not.toHaveBeenCalled();
  });
});
