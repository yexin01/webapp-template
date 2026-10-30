import { render, screen, waitFor } from "@testing-library/react";

import { ApiError } from "./api/errors";
import { DiagnosticsAccessProvider, useDiagnosticsAccess } from "./diagnostics-access";

const auth = vi.hoisted(() => ({ userId: "user_a" as string | null }));
const api = vi.hoisted(() => ({ integrations: vi.fn() }));
vi.mock("@clerk/nextjs", () => ({ useAuth: () => auth }));
vi.mock("@/lib/api/use-api", () => ({ useApi: () => api }));

const notFound = new ApiError(404, "NOT_FOUND", "Not found.", "req1");

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
  api.integrations.mockReset().mockResolvedValue({ environment: "local", integrations: [] });
});

describe("diagnostics access", () => {
  it("grants access only once the API has listed the integrations for the user", async () => {
    render(view());
    expect(screen.getByText("customer")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("operator")).toBeInTheDocument());
    expect(api.integrations).toHaveBeenCalledOnce();
  });

  it.each([
    ["answers 404, as it does for anyone who is not an operator", notFound],
    ["answers 401", new ApiError(401, "UNAUTHENTICATED", "Expired.", null)],
    ["cannot be reached", ApiError.network()],
    ["fails in a way the client does not know", new Error("boom")],
  ])("shows nothing when the API %s", async (_label, error) => {
    api.integrations.mockRejectedValue(error);
    render(view());
    await waitFor(() => expect(api.integrations).toHaveBeenCalledOnce());
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.getByText("customer")).toBeInTheDocument();
  });

  it("asks nothing of the API while signed out", async () => {
    auth.userId = null;
    render(view());
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(api.integrations).not.toHaveBeenCalled();
    expect(screen.getByText("customer")).toBeInTheDocument();
  });

  it("does not carry one user's access over to the next user of the browser", async () => {
    const { rerender } = render(view());
    await waitFor(() => expect(screen.getByText("operator")).toBeInTheDocument());

    auth.userId = "user_b";
    api.integrations.mockRejectedValue(notFound);
    rerender(view());
    expect(screen.getByText("customer")).toBeInTheDocument();
    await waitFor(() => expect(api.integrations).toHaveBeenCalledTimes(2));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.getByText("customer")).toBeInTheDocument();
  });
});
