import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { Schemas } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import { CLERK_LOAD_BACKSTOP_MS } from "@/lib/use-resource";

import { ProjectsPanel } from "./projects-panel";

const api = vi.hoisted(() => ({
  listProjects: vi.fn(),
  createProject: vi.fn(),
  deleteProject: vi.fn(),
}));
// What Clerk says. The panel takes the tenant from it and nothing else: the role is there to be ignored.
const clerk = vi.hoisted(() => ({
  authLoaded: true,
  orgLoaded: true,
  organization: null as { id: string; name: string } | null,
  membership: null as { role: string } | null,
}));
vi.mock("@/lib/api/use-api", () => ({ useApi: () => api }));
vi.mock("next/navigation", () => ({ usePathname: () => "/dashboard" }));
vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ isLoaded: clerk.authLoaded, userId: "user_1" }),
  useClerk: () => ({ status: "ready" }),
  useOrganization: () => ({
    isLoaded: clerk.orgLoaded,
    organization: clerk.organization,
    membership: clerk.membership,
  }),
}));

function project(id: string, name: string): Schemas["Project"] {
  return { id, name, created_at: "2026-10-01T00:00:00Z", organization_scoped: false };
}

const mayAll = { create: true, delete: true };

/** A page as the API answers it; `can` is its decision for the caller. */
function listing(
  items: Schemas["Project"][],
  nextCursor: string | null = null,
  can: Schemas["ProjectCapabilities"] = mayAll,
): Schemas["ProjectPage"] {
  return { items, next_cursor: nextCursor, can };
}

const expired = new ApiError(401, "UNAUTHENTICATED", "Token expired.", "req1");

beforeEach(() => {
  clerk.authLoaded = true;
  clerk.orgLoaded = true;
  clerk.organization = null;
  clerk.membership = null;
  api.listProjects.mockReset().mockResolvedValue(listing([project("p1", "Alpha")], "c2"));
  api.createProject.mockReset();
  api.deleteProject.mockReset();
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

async function renderPanel() {
  render(<ProjectsPanel />);
  await screen.findByText("Alpha");
  return screen.getByLabelText("New project name");
}

/** The persistent live region that announces results. */
const status = () => screen.getAllByRole("status").find((element) => element.tagName === "P");

describe("projects panel list", () => {
  it("lists a project created here once, even when a later page returns it", async () => {
    const fresh = project("p9", "Fresh");
    api.createProject.mockResolvedValue(fresh);
    const consoleError = vi.spyOn(console, "error");
    const input = await renderPanel();
    await userEvent.type(input, "Fresh");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByText("Fresh")).toBeInTheDocument();

    api.listProjects.mockResolvedValueOnce(listing([fresh, project("p2", "Gamma")]));
    await userEvent.click(screen.getByRole("button", { name: "Load more" }));
    expect(await screen.findByText("Gamma")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem").map((item) => item.firstChild?.textContent)).toEqual([
      "Fresh",
      "Alpha",
      "Gamma",
    ]);
    expect(consoleError.mock.calls.flat().join(" ")).not.toMatch(/same key/);
  });

  it("counts characters as the server does: 120 emoji fit, 121 characters do not", async () => {
    api.createProject.mockImplementation(async (_orgId: string | null, name: string) => project("p9", name));
    const input = await renderPanel();
    // 120 characters, but 240 UTF-16 units.
    const fits = "\u{1F600}".repeat(120);
    await userEvent.click(input);
    await userEvent.paste(fits);
    expect(input).toHaveValue(fits);
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(api.createProject).toHaveBeenCalledWith(null, fits);

    await userEvent.click(input);
    await userEvent.paste("a".repeat(121));
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByText("Use at most 120 characters.")).toBeInTheDocument();
    expect(api.createProject).toHaveBeenCalledOnce();
  });
});

describe("projects panel shows the API's decisions, never a role's (ARCH-03)", () => {
  const onlyReading = { create: false, delete: false };

  it("offers nothing to create until the list says the caller may", async () => {
    let answer: (page: Schemas["ProjectPage"]) => void = () => undefined;
    api.listProjects.mockReturnValueOnce(new Promise((resolve) => (answer = resolve)));
    render(<ProjectsPanel />);
    expect(screen.getByText("Loading projects…")).toBeInTheDocument();
    expect(screen.queryByLabelText("New project name")).toBeNull();
    expect(screen.queryByRole("button", { name: "Create" })).toBeNull();

    await act(async () => answer(listing([project("p1", "Alpha")])));
    expect(screen.getByLabelText("New project name")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create" })).toBeInTheDocument();
  });

  it.each([
    ["neither create nor delete", onlyReading, { form: false, remove: false }],
    ["create but not delete", { create: true, delete: false }, { form: true, remove: false }],
    ["delete but not create", { create: false, delete: true }, { form: false, remove: true }],
    ["both", mayAll, { form: true, remove: true }],
  ])("shows what a caller who may %s is offered, and no more", async (_case, can, shown) => {
    api.listProjects.mockResolvedValue(listing([project("p1", "Alpha")], null, can));
    render(<ProjectsPanel />);
    expect(await screen.findByText("Alpha")).toBeInTheDocument();
    expect(screen.queryByLabelText("New project name") !== null).toBe(shown.form);
    expect(screen.queryByRole("button", { name: "Create" }) !== null).toBe(shown.form);
    expect(screen.queryByRole("button", { name: "Delete Alpha" }) !== null).toBe(shown.remove);
  });

  it.each([
    ["an administrator the API does not let delete", "org:admin", { create: true, delete: false }, false],
    ["a member the API lets delete", "org:member", { create: true, delete: true }, true],
    ["a user Clerk gives no role", undefined, { create: true, delete: true }, true],
  ])("shows Delete for %s as the API says, whatever Clerk's role is", async (_case, role, can, shown) => {
    clerk.organization = { id: "org_1", name: "Acme" };
    clerk.membership = role ? { role } : null;
    api.listProjects.mockResolvedValue(listing([project("p1", "Alpha")], null, can));
    render(<ProjectsPanel />);
    expect(await screen.findByText("Alpha")).toBeInTheDocument();
    expect(api.listProjects).toHaveBeenCalledWith("org_1");
    expect(screen.queryByRole("button", { name: "Delete Alpha" }) !== null).toBe(shown);
  });

  it("offers a Delete the API allows on the projects that Load more adds", async () => {
    api.listProjects.mockResolvedValue(
      listing([project("p1", "Alpha")], "c2", { create: false, delete: true }),
    );
    render(<ProjectsPanel />);
    await screen.findByText("Alpha");
    api.listProjects.mockResolvedValueOnce(
      listing([project("p2", "Gamma")], null, { create: false, delete: true }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Load more" }));
    expect(await screen.findByRole("button", { name: "Delete Gamma" })).toBeInTheDocument();
    expect(screen.queryByLabelText("New project name")).toBeNull();
  });

  it("suggests creating the first project only to a caller who may", async () => {
    api.listProjects.mockResolvedValue(listing([], null, onlyReading));
    const { unmount } = render(<ProjectsPanel />);
    expect(await screen.findByText("No projects yet")).toBeInTheDocument();
    expect(screen.queryByText("Create your first project above.")).toBeNull();
    unmount();

    api.listProjects.mockResolvedValue(listing([]));
    render(<ProjectsPanel />);
    expect(await screen.findByText("Create your first project above.")).toBeInTheDocument();
  });

  it("offers nothing to create when the list cannot be loaded", async () => {
    api.listProjects.mockRejectedValue(ApiError.network());
    render(<ProjectsPanel />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Temporarily unavailable");
    expect(screen.queryByLabelText("New project name")).toBeNull();
  });
});

describe("projects panel tenant", () => {
  it("lists nothing until the tenant is known, then only that tenant's projects", async () => {
    // The session is there before the organization: a list for the personal workspace would be the wrong one.
    clerk.orgLoaded = false;
    const { rerender } = render(<ProjectsPanel />);
    expect(screen.getByText("Loading projects…")).toBeInTheDocument();
    expect(api.listProjects).not.toHaveBeenCalled();

    clerk.orgLoaded = true;
    clerk.organization = { id: "org_1", name: "Acme" };
    rerender(<ProjectsPanel />);
    expect(await screen.findByText("Alpha")).toBeInTheDocument();
    expect(api.listProjects).toHaveBeenCalledOnce();
    expect(api.listProjects).toHaveBeenCalledWith("org_1");
  });

  it("shows nothing of the previous organization's after a switch", async () => {
    api.listProjects.mockImplementation(async (orgId: string | null) =>
      orgId === "org_1" ? listing([project("a1", "Acme's plan")]) : listing([project("g1", "Globex's plan")]),
    );
    clerk.organization = { id: "org_1", name: "Acme" };
    const { rerender } = render(<ProjectsPanel />);
    expect(await screen.findByText("Acme's plan")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("New project name"), "half typed");

    clerk.organization = { id: "org_2", name: "Globex" };
    rerender(<ProjectsPanel />);
    expect(screen.queryByText("Acme's plan")).toBeNull();
    expect(await screen.findByText("Globex's plan")).toBeInTheDocument();
    expect(screen.getByLabelText("New project name")).toHaveValue("");
    expect(api.listProjects).toHaveBeenLastCalledWith("org_2");
  });

  it("offers a reload instead of loading forever when Clerk never loads (§16.4)", () => {
    vi.useFakeTimers();
    try {
      clerk.authLoaded = false;
      clerk.orgLoaded = false;
      const reloadPage = vi.fn();
      vi.spyOn(window, "location", "get").mockReturnValue({ ...window.location, reload: reloadPage });
      render(<ProjectsPanel />);
      expect(screen.getByText("Loading projects…")).toBeInTheDocument();

      act(() => vi.advanceTimersByTime(CLERK_LOAD_BACKSTOP_MS));
      expect(screen.getByRole("alert")).toHaveTextContent("Sign-in unavailable");
      act(() => screen.getByRole("button", { name: "Try again" }).click());
      expect(reloadPage).toHaveBeenCalledOnce();
      expect(api.listProjects).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
      vi.restoreAllMocks();
    }
  });
});

describe("projects panel delete", () => {
  it("disables Delete while its request runs, so a second click sends nothing", async () => {
    let finish: () => void = () => undefined;
    api.deleteProject.mockReturnValue(new Promise<void>((resolve) => (finish = resolve)));
    await renderPanel();
    const button = screen.getByRole("button", { name: "Delete Alpha" });
    await userEvent.click(button);
    expect(button).toBeDisabled();
    await userEvent.click(button);
    expect(api.deleteProject).toHaveBeenCalledOnce();

    await act(async () => finish());
    expect(screen.queryByText("Alpha")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("removes a project that was already deleted elsewhere instead of reporting an error", async () => {
    api.deleteProject.mockRejectedValue(new ApiError(404, "NOT_FOUND", "Not found.", null));
    await renderPanel();
    await userEvent.click(screen.getByRole("button", { name: "Delete Alpha" }));
    await waitFor(() => expect(screen.queryByText("Alpha")).toBeNull());
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("projects panel keyboard focus and announcements (FE-09)", () => {
  it("moves focus to the next project and announces a delete", async () => {
    api.listProjects.mockResolvedValue(listing([project("p1", "Alpha"), project("p2", "Bravo")]));
    api.deleteProject.mockResolvedValue(undefined);
    await renderPanel();
    screen.getByRole("button", { name: "Delete Alpha" }).focus();
    await userEvent.keyboard("{Enter}");
    await waitFor(() => expect(screen.queryByText("Alpha")).toBeNull());
    expect(screen.getByRole("button", { name: "Delete Bravo" })).toHaveFocus();
    expect(status()).toHaveTextContent("Deleted “Alpha”.");
  });

  it("moves focus to the heading when the last project is deleted", async () => {
    api.listProjects.mockResolvedValue(listing([project("p1", "Alpha")]));
    api.deleteProject.mockResolvedValue(undefined);
    await renderPanel();
    screen.getByRole("button", { name: "Delete Alpha" }).focus();
    await userEvent.keyboard("{Enter}");
    expect(await screen.findByText("No projects yet")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Projects" })).toHaveFocus();
  });

  it("returns focus to Delete when the delete fails", async () => {
    api.deleteProject.mockRejectedValue(
      new ApiError(403, "FORBIDDEN", "Only admins can delete projects.", null),
    );
    await renderPanel();
    const button = screen.getByRole("button", { name: "Delete Alpha" });
    button.focus();
    await userEvent.keyboard("{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("Not allowed");
    expect(button).toHaveFocus();
  });

  it("announces a created project", async () => {
    api.createProject.mockResolvedValue(project("p9", "Beta"));
    const input = await renderPanel();
    await userEvent.type(input, "Beta{Enter}");
    expect(await screen.findByText("Beta")).toBeInTheDocument();
    expect(status()).toHaveTextContent("Created “Beta”.");
    expect(input).toHaveFocus();
  });

  it("keeps focus in the list when the last page loads", async () => {
    await renderPanel();
    api.listProjects.mockResolvedValueOnce(listing([project("p2", "Gamma")]));
    screen.getByRole("button", { name: "Load more" }).focus();
    await userEvent.keyboard("{Enter}");
    expect(await screen.findByText("Gamma")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete Gamma" })).toHaveFocus();
  });

  it("keeps focus on Load more while more pages remain", async () => {
    await renderPanel();
    api.listProjects.mockResolvedValueOnce(listing([project("p2", "Gamma")], "c3"));
    const loadMore = screen.getByRole("button", { name: "Load more" });
    loadMore.focus();
    await userEvent.keyboard("{Enter}");
    expect(await screen.findByText("Gamma")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Load more" })).toHaveFocus();
  });

  it("keeps focus when Try again recovers a failed Load more", async () => {
    await renderPanel();
    api.listProjects.mockRejectedValueOnce(ApiError.network());
    await userEvent.click(screen.getByRole("button", { name: "Load more" }));
    const retry = within(await screen.findByRole("alert")).getByRole("button", { name: "Try again" });
    api.listProjects.mockResolvedValueOnce(listing([project("p2", "Gamma")]));
    retry.focus();
    await userEvent.keyboard("{Enter}");
    expect(await screen.findByText("Gamma")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete Gamma" })).toHaveFocus();
  });

  it("keeps focus when Try again reloads a failed first page", async () => {
    api.listProjects.mockRejectedValueOnce(ApiError.network());
    render(<ProjectsPanel />);
    const retry = await screen.findByRole("button", { name: "Try again" });
    retry.focus();
    await userEvent.keyboard("{Enter}");
    expect(await screen.findByText("Alpha")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Projects" })).toHaveFocus();
  });
});

describe("projects panel focus while a request runs (FE-09)", () => {
  /** Settles a request the test controls. */
  function pending<T>() {
    let settle: { resolve: (value: T) => void; reject: (error: unknown) => void } | undefined;
    const promise = new Promise<T>((resolve, reject) => (settle = { resolve, reject }));
    return { promise, settle: () => settle! };
  }

  /** The user moves on to the name field and types while the request runs. */
  async function typeAName() {
    const input = screen.getByLabelText("New project name");
    await userEvent.click(input);
    await userEvent.keyboard("Next pro");
    expect(input).toHaveFocus();
    return input;
  }

  beforeEach(() => {
    api.listProjects.mockResolvedValue(listing([project("p1", "Alpha"), project("p2", "Bravo")], "c2"));
  });

  it("leaves focus where the user moved it while a delete ran", async () => {
    const request = pending<void>();
    api.deleteProject.mockReturnValue(request.promise);
    await renderPanel();
    screen.getByRole("button", { name: "Delete Alpha" }).focus();
    await userEvent.keyboard("{Enter}");
    const input = await typeAName();

    await act(async () => request.settle().resolve());
    expect(screen.queryByText("Alpha")).toBeNull();
    expect(input).toHaveFocus();
    expect(input).toHaveValue("Next pro");
  });

  it("leaves focus where the user moved it while a failing delete ran", async () => {
    const request = pending<void>();
    api.deleteProject.mockReturnValue(request.promise);
    await renderPanel();
    screen.getByRole("button", { name: "Delete Alpha" }).focus();
    await userEvent.keyboard("{Enter}");
    const input = await typeAName();

    await act(async () => request.settle().reject(new ApiError(403, "FORBIDDEN", "Only admins.", null)));
    expect(screen.getByRole("alert")).toHaveTextContent("Not allowed");
    expect(input).toHaveFocus();
  });

  it("leaves focus where the user moved it while Load more ran", async () => {
    await renderPanel();
    const request = pending<Schemas["ProjectPage"]>();
    api.listProjects.mockReturnValueOnce(request.promise);
    screen.getByRole("button", { name: "Load more" }).focus();
    await userEvent.keyboard("{Enter}");
    const input = await typeAName();

    await act(async () => request.settle().resolve(listing([project("p3", "Gamma")])));
    expect(screen.getByText("Gamma")).toBeInTheDocument();
    expect(input).toHaveFocus();
  });

  it("leaves focus where the user moved it while a failing Load more ran", async () => {
    await renderPanel();
    const request = pending<Schemas["ProjectPage"]>();
    api.listProjects.mockReturnValueOnce(request.promise);
    screen.getByRole("button", { name: "Load more" }).focus();
    await userEvent.keyboard("{Enter}");
    const input = await typeAName();

    await act(async () => request.settle().reject(ApiError.network()));
    expect(screen.getByRole("alert")).toHaveTextContent("Temporarily unavailable");
    expect(input).toHaveFocus();
  });
});

describe("projects panel privacy (OBS-02)", () => {
  it("keeps project names out of the delete button's attributes, which error reports copy", async () => {
    await renderPanel();
    const button = screen.getByRole("button", { name: "Delete Alpha" });
    expect([...button.attributes].map((attribute) => attribute.value).join(" ")).not.toContain("Alpha");
  });
});

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

    api.listProjects.mockResolvedValueOnce(listing([project("p2", "Gamma")]));
    await userEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Gamma")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(api.listProjects).toHaveBeenLastCalledWith(null, "c2");
  });

  it("starts over from the first page when the server rejects the Load more cursor", async () => {
    await renderPanel();
    api.listProjects.mockRejectedValueOnce(
      new ApiError(400, "INVALID_REQUEST", "The pagination cursor is invalid.", null),
    );
    await userEvent.click(screen.getByRole("button", { name: "Load more" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Request not accepted");
    // Sending the same cursor again would fail the same way, so Load more is gone until the list starts over.
    expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();
    expect(within(alert).getByRole("button", { name: "Try again" })).toHaveFocus();

    api.listProjects.mockResolvedValueOnce(listing([project("p1", "Alpha"), project("p2", "Gamma")]));
    await userEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Gamma")).toBeInTheDocument();
    expect(api.listProjects).toHaveBeenLastCalledWith(null);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Projects" })).toHaveFocus();
  });
});
