import * as Sentry from "@sentry/nextjs";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import RouteError from "./error";

vi.mock("@sentry/nextjs", () => ({ captureException: vi.fn() }));

const DETAIL = "connection to db.internal:5432 refused";
const failure = (digest?: string) => Object.assign(new Error(DETAIL), digest ? { digest } : {});

/** FE-08: a failure is isolated to its route, reported, and never shown. */
describe("the route error boundary", () => {
  it("says something went wrong, with the server's reference, and tries again when asked", async () => {
    const reset = vi.fn();
    render(<RouteError error={failure("digest-1234")} reset={reset} />);
    const alert = screen.getByRole("alert");
    expect(screen.getByRole("heading", { level: 1, name: "Something went wrong" })).toBeInTheDocument();
    expect(alert).toHaveTextContent("This part of the page failed to load. You can try again.");
    expect(alert).toHaveTextContent("Reference: digest-1234");
    expect(reset).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(reset).toHaveBeenCalledOnce();
  });

  it("shows no reference when the error has none, and never the error's own message", () => {
    render(<RouteError error={failure()} reset={vi.fn()} />);
    const alert = screen.getByRole("alert");
    expect(alert).not.toHaveTextContent("Reference");
    expect(alert).not.toHaveTextContent("db.internal");
    expect(alert).not.toHaveTextContent("refused");
  });

  it("reports each error to Sentry once", () => {
    const first = failure("a");
    const second = failure("b");
    const { rerender } = render(<RouteError error={first} reset={vi.fn()} />);
    rerender(<RouteError error={first} reset={vi.fn()} />);
    expect(Sentry.captureException).toHaveBeenCalledExactlyOnceWith(first);
    rerender(<RouteError error={second} reset={vi.fn()} />);
    expect(Sentry.captureException).toHaveBeenCalledTimes(2);
    expect(Sentry.captureException).toHaveBeenLastCalledWith(second);
  });
});
