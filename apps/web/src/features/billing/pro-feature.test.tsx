import { act, render, screen } from "@testing-library/react";

import { track } from "@/lib/analytics";
import { ApiError } from "@/lib/api/errors";
import { CLERK_LOAD_BACKSTOP_MS } from "@/lib/use-resource";

import { ProFeature } from "./pro-feature";

const api = vi.hoisted(() => ({ proInsight: vi.fn() }));
const auth = vi.hoisted(() => ({ isLoaded: true, userId: "user_1" as string | undefined }));
const clerk = vi.hoisted(() => ({ organization: null as { id: string } | null }));
vi.mock("@/lib/api/use-api", () => ({ useApi: () => api }));
vi.mock("@/lib/analytics", () => ({ track: vi.fn() }));
vi.mock("next/navigation", () => ({ usePathname: () => "/pro" }));
vi.mock("@clerk/nextjs", () => ({
  useAuth: () => auth,
  useClerk: () => ({ status: "loading" }),
  useOrganization: () => ({ isLoaded: auth.isLoaded, organization: clerk.organization }),
}));

const insight = (message: string) => ({ message, generated_at: "2026-10-01T12:00:00Z" });
const needsPro = () => new ApiError(403, "ENTITLEMENT_REQUIRED", "This feature requires the Pro plan.", null);
/** A promise the test settles when it chooses: an answer that has not arrived yet. */
function deferred<T>() {
  let resolve: (value: T) => void = () => undefined;
  let reject: (reason: unknown) => void = () => undefined;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  auth.isLoaded = true;
  auth.userId = "user_1";
  clerk.organization = null;
  api.proInsight.mockReset();
  vi.mocked(track).mockClear();
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("Pro feature", () => {
  it("offers a reload instead of loading forever when Clerk never loads (§16.4)", () => {
    vi.useFakeTimers();
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

  it("shows a Pro user the insight, not the paywall", async () => {
    api.proInsight.mockResolvedValue(insight("Your insight."));
    render(<ProFeature />);
    expect(await screen.findByRole("heading", { name: "Pro insight" })).toBeInTheDocument();
    expect(screen.getByText("Your insight.")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Upgrade to Pro" })).toBeNull();
    expect(api.proInsight).toHaveBeenCalledExactlyOnceWith(null);
  });

  it("shows a Free user in the personal workspace the paywall, with the personal copy", async () => {
    api.proInsight.mockRejectedValue(needsPro());
    render(<ProFeature />);
    expect(await screen.findByRole("heading", { name: "Upgrade to Pro" })).toBeInTheDocument();
    expect(screen.getByText(/part of the Pro plan/)).toBeInTheDocument();
    expect(screen.queryByText(/your own Pro plan/)).toBeNull();
    expect(screen.getByRole("link", { name: "See plans" })).toHaveAttribute("href", "/billing");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(track).toHaveBeenCalledWith("paywall_viewed", { feature: "pro_insight" });
    expect(api.proInsight).toHaveBeenCalledExactlyOnceWith(null);
  });

  it("tells a Free user in an organization that Pro is personal and not shared", async () => {
    clerk.organization = { id: "org_a" };
    api.proInsight.mockRejectedValue(needsPro());
    render(<ProFeature />);
    expect(await screen.findByText(/your own Pro plan/)).toBeInTheDocument();
    expect(screen.getByText(/not shared with other members/)).toBeInTheDocument();
    expect(screen.queryByText(/part of the Pro plan/)).toBeNull();
    expect(screen.getByRole("link", { name: "See plans" })).toHaveAttribute("href", "/billing");
    expect(api.proInsight).toHaveBeenCalledExactlyOnceWith("org_a");
  });

  it.each([
    ["a refusal that is not about the plan", new ApiError(403, "FORBIDDEN", "No.", null), "Not allowed"],
    ["a server error", new ApiError(500, "INTERNAL_ERROR", "Oops.", "req-1"), "Something went wrong"],
    ["a network failure", ApiError.network(), "Temporarily unavailable"],
  ])("shows %s as an error, never as the paywall", async (_label, error, title) => {
    api.proInsight.mockRejectedValue(error);
    render(<ProFeature />);
    expect(await screen.findByRole("alert")).toHaveTextContent(title);
    expect(screen.queryByRole("heading", { name: "Upgrade to Pro" })).toBeNull();
    expect(track).not.toHaveBeenCalled();
  });

  it("shows nothing of the previous organization's insight after a switch", async () => {
    clerk.organization = { id: "org_a" };
    const beta = deferred<ReturnType<typeof insight>>();
    api.proInsight.mockImplementation((orgId: string | null) =>
      orgId === "org_a" ? Promise.resolve(insight("Insight of Acme.")) : beta.promise,
    );
    const { rerender } = render(<ProFeature />);
    expect(await screen.findByText("Insight of Acme.")).toBeInTheDocument();

    clerk.organization = { id: "org_b" };
    rerender(<ProFeature />);
    // Beta's answer has not arrived: Acme's insight is not shown in its place.
    expect(screen.queryByText("Insight of Acme.")).toBeNull();
    expect(screen.getByRole("status")).toBeInTheDocument();

    await act(async () => beta.resolve(insight("Insight of Beta.")));
    expect(screen.getByText("Insight of Beta.")).toBeInTheDocument();
    expect(api.proInsight.mock.calls.map(([orgId]) => orgId)).toEqual(["org_a", "org_b"]);
  });

  it("hides the previous workspace's paywall until the next workspace has answered", async () => {
    const acme = deferred<ReturnType<typeof insight>>();
    api.proInsight.mockImplementation((orgId: string | null) =>
      orgId === null ? Promise.reject(needsPro()) : acme.promise,
    );
    const { rerender } = render(<ProFeature />);
    expect(await screen.findByText(/part of the Pro plan/)).toBeInTheDocument();

    clerk.organization = { id: "org_a" };
    rerender(<ProFeature />);
    expect(screen.queryByRole("heading", { name: "Upgrade to Pro" })).toBeNull();
    expect(screen.getByRole("status")).toBeInTheDocument();

    await act(async () => acme.reject(needsPro()));
    expect(screen.getByText(/your own Pro plan/)).toBeInTheDocument();
    expect(screen.queryByText(/part of the Pro plan/)).toBeNull();
  });
});
