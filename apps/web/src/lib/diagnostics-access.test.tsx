import { render, screen, waitFor } from "@testing-library/react";

import { ApiError } from "./api/errors";
import { DiagnosticsAccessProvider, useDiagnosticsAccess } from "./diagnostics-access";

const auth = vi.hoisted(() => ({ userId: "user_a" as string | null }));
const api = vi.hoisted(() => ({ diagnosticsAccess: vi.fn(), integrations: vi.fn() }));
vi.mock("@clerk/nextjs", () => ({ useAuth: () => auth }));
vi.mock("@/lib/api/use-api", () => ({ useApi: () => api }));

function Probe() {
  return <p>{useDiagnosticsAccess() ? "operator" : "customer"}</p>;
}

function view() {
  return (
    <DiagnosticsAccessProvider>
      <Probe />
    </DiagnosticsAccessProvider>
  );
}

beforeEach(() => {
  auth.userId = "user_a";
  api.diagnosticsAccess.mockReset().mockResolvedValue({ available: true });
  api.integrations.mockReset();
});

describe("diagnostics access", () => {
  it("grants access only once the API has said the tools are available to the user", async () => {
    render(view());
    expect(screen.getByText("customer")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("operator")).toBeInTheDocument());
    expect(api.diagnosticsAccess).toHaveBeenCalledOnce();
  });

  it("asks the access probe, which never fails for a customer, and not the report that answers them 404", async () => {
    render(view());
    await waitFor(() => expect(screen.getByText("operator")).toBeInTheDocument());
    expect(api.integrations).not.toHaveBeenCalled();
  });

  it("shows nothing to a user the API says the tools are not available to", async () => {
    api.diagnosticsAccess.mockResolvedValue({ available: false });
    render(view());
    await waitFor(() => expect(api.diagnosticsAccess).toHaveBeenCalledOnce());
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.getByText("customer")).toBeInTheDocument();
  });

  it.each([
    ["answers 401", new ApiError(401, "UNAUTHENTICATED", "Expired.", null)],
    [
      "answers 404, as an API that does not know the probe would",
      new ApiError(404, "NOT_FOUND", "Not found.", "req1"),
    ],
    ["cannot be reached", ApiError.network()],
    ["fails in a way the client does not know", new Error("boom")],
  ])("shows nothing when the API %s", async (_label, error) => {
    api.diagnosticsAccess.mockRejectedValue(error);
    render(view());
    await waitFor(() => expect(api.diagnosticsAccess).toHaveBeenCalledOnce());
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.getByText("customer")).toBeInTheDocument();
  });

  it("asks nothing of the API while signed out", async () => {
    auth.userId = null;
    render(view());
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(api.diagnosticsAccess).not.toHaveBeenCalled();
    expect(screen.getByText("customer")).toBeInTheDocument();
  });

  it("does not carry one user's access over to the next user of the browser", async () => {
    const { rerender } = render(view());
    await waitFor(() => expect(screen.getByText("operator")).toBeInTheDocument());

    auth.userId = "user_b";
    api.diagnosticsAccess.mockResolvedValue({ available: false });
    rerender(view());
    expect(screen.getByText("customer")).toBeInTheDocument();
    await waitFor(() => expect(api.diagnosticsAccess).toHaveBeenCalledTimes(2));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.getByText("customer")).toBeInTheDocument();
  });
});
