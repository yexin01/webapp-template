import { render, screen } from "@testing-library/react";

import { SiteHeader } from "./site-header";

const access = vi.hoisted(() => ({ granted: false }));
vi.mock("@/lib/diagnostics-access", () => ({ useDiagnosticsAccess: () => access.granted }));
vi.mock("@/lib/use-resource", () => ({ reloadPage: vi.fn(), useClerkUnavailable: () => false }));
vi.mock("@clerk/nextjs", () => ({
  Show: ({ when, children }: { when: string; children: React.ReactNode }) =>
    when === "signed-in" ? <>{children}</> : null,
  OrganizationSwitcher: () => null,
  SignInButton: () => null,
  SignUpButton: () => null,
  UserButton: () => null,
}));

describe("site header", () => {
  it("links the customer pages to every signed-in user, without the operator tools", () => {
    access.granted = false;
    render(<SiteHeader />);
    for (const name of ["Dashboard", "Pro feature", "Billing"]) {
      expect(screen.getByRole("link", { name })).toBeInTheDocument();
    }
    expect(screen.queryByRole("link", { name: "Integrations" })).toBeNull();
  });

  it("links Integrations when the API lets the user use the diagnostics", () => {
    access.granted = true;
    render(<SiteHeader />);
    expect(screen.getByRole("link", { name: "Integrations" })).toHaveAttribute("href", "/integrations");
  });
});
