import { render, screen } from "@testing-library/react";

import NotFound, { metadata } from "./not-found";

/** FE-07: a proper 404 page offers recoverable navigation. A signed-out visitor is sent to sign-in instead (layout.test.tsx). */
describe("the 404 page", () => {
  it("says the page does not exist and offers the way back", () => {
    render(<NotFound />);
    expect(screen.getByRole("heading", { level: 1, name: "Page not found" })).toBeInTheDocument();
    expect(screen.getByText("The page you are looking for does not exist or has moved.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go home" })).toHaveAttribute("href", "/");
    expect(screen.getByRole("link", { name: "Dashboard" })).toHaveAttribute("href", "/dashboard");
  });

  it("titles the document by what it is", () => {
    expect(metadata.title).toBe("Page not found");
  });
});
