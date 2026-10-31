import * as Sentry from "@sentry/nextjs";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import GlobalError from "./global-error";

vi.mock("@sentry/nextjs", () => ({ captureException: vi.fn() }));

const failure = () => Object.assign(new Error("connection to db.internal:5432 refused"), { digest: "d-1" });

/**
 * FE-08: when the root layout itself fails, this page replaces it. It must be a whole document, because no layout
 * is left around it, and it must not depend on anything that may be what failed (Clerk, the API, the stylesheet).
 */
describe("the global error boundary", () => {
  it("is a whole document that says the application failed to load, and tries again when asked", async () => {
    const reset = vi.fn();
    render(<GlobalError error={failure()} reset={reset} />, { container: document });
    expect(document.documentElement.lang).toBe("en");
    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Something went wrong" })).toBeInTheDocument();
    expect(screen.getByText("The application failed to load. Please try again.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(reset).toHaveBeenCalledOnce();
  });

  it("never shows the error's own message", () => {
    render(<GlobalError error={failure()} reset={vi.fn()} />, { container: document });
    expect(document.body).not.toHaveTextContent("db.internal");
    expect(document.body).not.toHaveTextContent("refused");
  });

  it("reports the error to Sentry", () => {
    const error = failure();
    render(<GlobalError error={error} reset={vi.fn()} />, { container: document });
    expect(Sentry.captureException).toHaveBeenCalledExactlyOnceWith(error);
  });
});
