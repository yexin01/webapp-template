import { render, screen } from "@testing-library/react";

import { EXPECTED_PUBLIC_ROUTES, routes } from "@/lib/app-routes.testing";

import RootLayout from "./layout";

/**
 * Default-deny page access, as the browser sees it when the proxy did not run (FE-05): the real root layout
 * around a page of every route src/app answers, for a visitor Clerk reports signed out. A page that is not
 * meant to be public must render nothing of its own and send the visitor to sign-in, so a new page cannot
 * bypass the gate by being created. The allowlist is page-access.ts, the expectations are
 * EXPECTED_PUBLIC_ROUTES (app-routes.testing.ts): the two are checked against each other in page-access.test.ts.
 */
const auth = vi.hoisted(() => ({ isLoaded: true, isSignedIn: false }));
const navigation = vi.hoisted(() => ({ pathname: "/" }));
const router = vi.hoisted(() => ({ replace: vi.fn() }));

vi.mock("@clerk/nextjs", () => ({
  ClerkProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useAuth: () => auth,
  useClerk: () => ({ status: "ready" }),
}));
vi.mock("next/navigation", () => ({ usePathname: () => navigation.pathname, useRouter: () => router }));
vi.mock("@/lib/analytics-provider", () => ({ AnalyticsProvider: () => null }));
vi.mock("@/lib/diagnostics-access", () => ({
  DiagnosticsAccessProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock("@/ui/site-header", () => ({ SiteHeader: () => null }));

const PAGE = "what the page itself renders";

function visit(pathname: string) {
  navigation.pathname = pathname;
  render(
    <RootLayout>
      <p>{PAGE}</p>
    </RootLayout>,
    { container: document },
  );
}

beforeEach(() => {
  auth.isLoaded = true;
  auth.isSignedIn = false;
  router.replace.mockReset();
});

const everyUrl = routes().flatMap((route) =>
  route.samples.map((url) => ({
    file: route.file,
    url,
    public: EXPECTED_PUBLIC_ROUTES.includes(route.pattern),
  })),
);

describe("the root layout with a signed-out visitor", () => {
  it("covers the application's pages", () => {
    expect(everyUrl.map((entry) => entry.url)).toEqual(
      expect.arrayContaining(["/", "/dashboard", "/sign-in"]),
    );
  });

  it.each(everyUrl)("serves $url ($file) only if it is meant to be public", ({ url, public: isPublic }) => {
    visit(url);
    if (isPublic) {
      expect(screen.getByText(PAGE)).toBeInTheDocument();
      expect(router.replace).not.toHaveBeenCalled();
    } else {
      expect(screen.queryByText(PAGE), "a page renders for a visitor who is not signed in").toBeNull();
      expect(router.replace).toHaveBeenCalledExactlyOnceWith(
        `/sign-in?redirect_url=${encodeURIComponent(url)}`,
      );
    }
  });

  it("also keeps a path that no page answers (the 404 page) behind sign-in", () => {
    visit("/no-such-page");
    expect(screen.queryByText(PAGE)).toBeNull();
    expect(router.replace).toHaveBeenCalledOnce();
  });

  it("shows a protected page to a signed-in user", () => {
    auth.isSignedIn = true;
    visit("/dashboard");
    expect(screen.getByText(PAGE)).toBeInTheDocument();
    expect(router.replace).not.toHaveBeenCalled();
  });
});
