import { act, render, screen } from "@testing-library/react";

import { CLERK_LOAD_BACKSTOP_MS } from "@/lib/use-resource";

import { ProFeature } from "./pro-feature";

const api = vi.hoisted(() => ({ proInsight: vi.fn() }));
const auth = vi.hoisted(() => ({ isLoaded: true, userId: "user_1" as string | undefined }));
vi.mock("@/lib/api/use-api", () => ({ useApi: () => api }));
vi.mock("@/lib/analytics", () => ({ track: vi.fn() }));
vi.mock("next/navigation", () => ({ usePathname: () => "/pro" }));
vi.mock("@clerk/nextjs", () => ({
  useAuth: () => auth,
  useClerk: () => ({ status: "loading" }),
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

    act(() => vi.advanceTimersByTime(CLERK_LOAD_BACKSTOP_MS));
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Sign-in unavailable");
    act(() => screen.getByRole("button", { name: "Try again" }).click());
    expect(reloadPage).toHaveBeenCalledOnce();
    expect(api.proInsight).not.toHaveBeenCalled();
  });
});
