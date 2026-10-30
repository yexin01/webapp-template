import { act, render, screen } from "@testing-library/react";

import { PageGate } from "./page-gate";

type ClerkStatus = "loading" | "ready" | "degraded" | "error";
const auth = vi.hoisted(() => ({
  isLoaded: false,
  isSignedIn: undefined as boolean | undefined,
  // Counts the reads, so a public page can be shown to never consult Clerk.
  reads: 0,
}));
const clerk = vi.hoisted(() => ({ status: "loading" as ClerkStatus }));
const navigation = vi.hoisted(() => ({ pathname: "/dashboard" }));
const router = vi.hoisted(() => ({ replace: vi.fn(), push: vi.fn() }));

vi.mock("@clerk/nextjs", () => ({
  useAuth: () => {
    auth.reads += 1;
    return { isLoaded: auth.isLoaded, isSignedIn: auth.isSignedIn };
  },
  useClerk: () => clerk,
}));
vi.mock("next/navigation", () => ({ usePathname: () => navigation.pathname, useRouter: () => router }));

const PAGE = "the page's own content";

function gated() {
  return (
    <PageGate>
      <p>{PAGE}</p>
    </PageGate>
  );
}

/** Clerk's states as a visitor meets them: still loading, loaded without a session, loaded with one. */
const CLERK_STATES = {
  loading: { isLoaded: false, isSignedIn: undefined },
  "signed out": { isLoaded: true, isSignedIn: false },
  "signed in": { isLoaded: true, isSignedIn: true },
} as const;

function clerkIs(state: keyof typeof CLERK_STATES) {
  Object.assign(auth, CLERK_STATES[state]);
}

afterEach(() => vi.restoreAllMocks());

beforeEach(() => {
  clerkIs("loading");
  auth.reads = 0;
  clerk.status = "loading";
  navigation.pathname = "/dashboard";
  router.replace.mockReset();
  window.history.replaceState({}, "", "/");
});

describe("page gate: a page that is not on the allowlist", () => {
  it("renders nothing of the page, and does not leave, while Clerk is loading", () => {
    render(gated());
    expect(screen.queryByText(PAGE)).toBeNull();
    expect(screen.getByRole("status")).toHaveTextContent("Loading…");
    expect(router.replace).not.toHaveBeenCalled();
  });

  it("renders nothing of the page for a signed-out visitor, and sends them to sign-in", () => {
    clerkIs("signed out");
    render(gated());
    expect(screen.queryByText(PAGE)).toBeNull();
    expect(screen.getByRole("status")).toHaveTextContent("Redirecting to sign in…");
    expect(router.replace).toHaveBeenCalledExactlyOnceWith("/sign-in?redirect_url=%2Fdashboard");
  });

  it("keeps the query string in the relative return path", () => {
    clerkIs("signed out");
    navigation.pathname = "/billing";
    window.history.replaceState({}, "", "/billing?plan=pro&from=email");
    render(gated());
    expect(router.replace).toHaveBeenCalledExactlyOnceWith(
      `/sign-in?redirect_url=${encodeURIComponent("/billing?plan=pro&from=email")}`,
    );
  });

  it("never sends an address that names another origin as the return path (SEC-14)", () => {
    clerkIs("signed out");
    navigation.pathname = "//evil.example.com/dashboard";
    render(gated());
    const [target] = router.replace.mock.calls[0] as [string];
    expect(new URL(target, "https://app.example.com").searchParams.get("redirect_url")).toBe("/");
  });

  it("shows the page to a signed-in user, and only then", () => {
    const { rerender } = render(gated());
    expect(screen.queryByText(PAGE)).toBeNull();
    clerkIs("signed in");
    rerender(gated());
    expect(screen.getByText(PAGE)).toBeInTheDocument();
    expect(router.replace).not.toHaveBeenCalled();
  });

  it("covers every page that is not listed, whatever its path", () => {
    clerkIs("signed out");
    for (const pathname of [
      "/billing",
      "/billing/success",
      "/pro",
      "/integrations",
      "/notes/1",
      "/no-such-page",
    ]) {
      navigation.pathname = pathname;
      router.replace.mockReset();
      const { unmount } = render(gated());
      expect(screen.queryByText(PAGE), pathname).toBeNull();
      expect(router.replace, pathname).toHaveBeenCalledExactlyOnceWith(
        `/sign-in?redirect_url=${encodeURIComponent(pathname)}`,
      );
      unmount();
    }
  });

  it("says so, with a way to reload, when Clerk cannot load, instead of waiting forever", () => {
    clerk.status = "error";
    const reload = vi.fn();
    vi.spyOn(window, "location", "get").mockReturnValue({ ...window.location, reload });
    render(gated());
    expect(screen.queryByText(PAGE)).toBeNull();
    expect(screen.getByRole("alert")).toHaveTextContent("Sign-in could not be loaded");
    act(() => screen.getByRole("button", { name: "Try again" }).click());
    expect(reload).toHaveBeenCalledOnce();
    expect(router.replace).not.toHaveBeenCalled();
  });

  describe("once it has been shown to a signed-in user", () => {
    it("is not taken away when the session ends meanwhile (FE-04, and Clerk's sign-out navigation is not raced)", () => {
      clerkIs("signed in");
      const { rerender } = render(gated());
      clerkIs("signed out");
      rerender(gated());
      expect(screen.getByText(PAGE)).toBeInTheDocument();
      expect(router.replace).not.toHaveBeenCalled();
    });

    it("is not unmounted by a moment in which Clerk reports that it is loading", () => {
      clerkIs("signed in");
      const { rerender } = render(gated());
      clerkIs("loading");
      rerender(gated());
      expect(screen.getByText(PAGE)).toBeInTheDocument();
    });

    it("does not open the next page for a visitor whose session has ended", () => {
      clerkIs("signed in");
      const { rerender } = render(gated());
      clerkIs("signed out");
      navigation.pathname = "/billing";
      rerender(gated());
      expect(screen.queryByText(PAGE)).toBeNull();
      expect(router.replace).toHaveBeenCalledExactlyOnceWith("/sign-in?redirect_url=%2Fbilling");
    });
  });
});

describe("page gate: a public page", () => {
  it.each(["/", "/sign-in", "/sign-in/factor-one", "/sign-up", "/sign-up/verify-email-address"])(
    "is left alone in every state of Clerk: %s",
    (pathname) => {
      navigation.pathname = pathname;
      for (const state of Object.keys(CLERK_STATES) as (keyof typeof CLERK_STATES)[]) {
        clerkIs(state);
        const { unmount } = render(gated());
        expect(screen.getByText(PAGE), `${pathname} while ${state}`).toBeInTheDocument();
        expect(screen.queryByRole("status")).toBeNull();
        unmount();
      }
      expect(router.replace).not.toHaveBeenCalled();
      // It is not even asked: nothing about a public page depends on Clerk loading.
      expect(auth.reads).toBe(0);
    },
  );

  it("is not held up by a Clerk that failed to load", () => {
    navigation.pathname = "/";
    clerk.status = "error";
    render(gated());
    expect(screen.getByText(PAGE)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
