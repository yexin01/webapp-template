import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { Schemas } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";

import { ProjectsPanel } from "./projects-panel";

const api = vi.hoisted(() => ({
  listProjects: vi.fn(),
  createProject: vi.fn(),
  deleteProject: vi.fn(),
}));
vi.mock("@/lib/api/use-api", () => ({ useApi: () => api }));
vi.mock("next/navigation", () => ({ usePathname: () => "/dashboard" }));
vi.mock("@clerk/nextjs", () => ({ useAuth: () => ({ isLoaded: true, userId: "user_1" }) }));

function project(id: string, name: string): Schemas["Project"] {
  return { id, name, created_at: "2026-10-01T00:00:00Z", organization_scoped: false };
}

const expired = new ApiError(401, "UNAUTHENTICATED", "Token expired.", "req1");

beforeEach(() => {
  api.listProjects.mockReset().mockResolvedValue({ items: [project("p1", "Alpha")], next_cursor: "c2" });
  api.createProject.mockReset();
  api.deleteProject.mockReset();
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

async function renderPanel() {
  render(<ProjectsPanel orgId={null} canDelete />);
  await screen.findByText("Alpha");
  return screen.getByLabelText("New project name");
}

describe("projects panel errors (FE-10)", () => {
  it("offers sign-in when the session expired while creating, keeping the typed name", async () => {
    api.createProject.mockRejectedValue(expired);
    const input = await renderPanel();
    await userEvent.type(input, "Beta");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Your session has ended");
    expect(within(alert).getByRole("link", { name: "Sign in" })).toHaveAttribute(
      "href",
      "/sign-in?redirect_url=%2Fdashboard",
    );
    expect(alert).not.toHaveTextContent("Token expired.");
    expect(input).not.toHaveAttribute("aria-invalid");
    expect(input).toHaveValue("Beta");
  });

  it("shows a rejected name next to the field", async () => {
    api.createProject.mockRejectedValue(
      new ApiError(422, "VALIDATION_FAILED", "Name is already taken.", null),
    );
    const input = await renderPanel();
    await userEvent.type(input, "Alpha");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByText("Name is already taken.")).toBeInTheDocument();
    expect(input).toHaveAttribute("aria-invalid", "true");
  });

  it("reports a failed delete without blaming the name field", async () => {
    api.deleteProject.mockRejectedValue(
      new ApiError(403, "FORBIDDEN", "Only admins can delete projects.", null),
    );
    const input = await renderPanel();
    await userEvent.click(screen.getByRole("button", { name: "Delete Alpha" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Not allowed");
    expect(input).not.toHaveAttribute("aria-invalid");
    expect(screen.getByText("Alpha")).toBeInTheDocument();
  });

  it("offers retry when loading more fails, and recovers", async () => {
    const input = await renderPanel();
    api.listProjects.mockRejectedValueOnce(ApiError.network());
    await userEvent.click(screen.getByRole("button", { name: "Load more" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Temporarily unavailable");
    expect(input).not.toHaveAttribute("aria-invalid");

    api.listProjects.mockResolvedValueOnce({ items: [project("p2", "Gamma")], next_cursor: null });
    await userEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Gamma")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(api.listProjects).toHaveBeenLastCalledWith(null, "c2");
  });
});
