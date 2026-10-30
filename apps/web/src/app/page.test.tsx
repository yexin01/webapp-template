import { render, screen } from "@testing-library/react";

import HomePage from "./page";

const access = vi.hoisted(() => ({ granted: false }));
vi.mock("@/lib/diagnostics-access", () => ({ useDiagnosticsAccess: () => access.granted }));
vi.mock("@clerk/nextjs", () => ({
  Show: ({ when, children }: { when: string; children: React.ReactNode }) =>
    when === "signed-in" ? <>{children}</> : null,
  SignUpButton: () => null,
}));

describe("home page", () => {
  it("offers the dashboard to a signed-in user, and the integration check only to an operator", () => {
    access.granted = false;
    const { unmount } = render(<HomePage />);
    expect(screen.getByRole("link", { name: "Open dashboard" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Check integrations" })).toBeNull();
    unmount();

    access.granted = true;
    render(<HomePage />);
    expect(screen.getByRole("link", { name: "Check integrations" })).toHaveAttribute("href", "/integrations");
  });
});
