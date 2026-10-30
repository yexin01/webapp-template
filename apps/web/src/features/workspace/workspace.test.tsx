import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { type Schemas } from "@/lib/api/client";
import { CLERK_LOAD_BACKSTOP_MS } from "@/lib/use-resource";

import { Workspace } from "./workspace";

type User = "user_a" | "user_b";
type Project = Schemas["Project"];

const auth = vi.hoisted(() => ({ isLoaded: true, userId: "user_a" as string | null | undefined }));
// Clerk's getToken is one stable callback per Clerk instance, so it does not
// change when a multi-session browser switches to another account.
const getToken = vi.hoisted(() => async () => `token-for-${auth.userId}`);
const server = vi.hoisted(() => ({
  plans: { user_a: "pro", user_b: "free" } as Record<string, "pro" | "free">,
  projects: { user_a: [], user_b: [] } as Record<string, Project[]>,
  // What the API says the caller may do; it is all that decides what the page offers.
  can: { create: true, delete: true },
  listCalls: [] as string[],
  entitlementCalls: 0,
}));
// What Clerk says about the active organization and the user's role there.
const clerk = vi.hoisted(() => ({
  organization: null as { id: string; name: string } | null,
  membership: null as { role: string } | null,
}));

vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ isLoaded: auth.isLoaded, userId: auth.userId, getToken }),
  useClerk: () => ({ status: "loading" }),
  useOrganization: () => ({ isLoaded: auth.isLoaded, ...clerk }),
}));
vi.mock("next/navigation", () => ({ usePathname: () => "/dashboard" }));
const access = vi.hoisted(() => ({ granted: false }));
vi.mock("@/lib/diagnostics-access", () => ({ useDiagnosticsAccess: () => access.granted }));
// The real useApi, over a fake API that answers for whoever's token a request carries.
vi.mock("@/lib/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/client")>();
  return {
    ...actual,
    createApiClient: (token: () => Promise<string | null>) => {
      const caller = async () => ((await token()) ?? "").replace("token-for-", "") as User;
      return {
        entitlement: async () => {
          server.entitlementCalls += 1;
          return { plan: server.plans[await caller()] };
        },
        listProjects: async () => {
          const user = await caller();
          server.listCalls.push(user);
          return { items: server.projects[user], next_cursor: null, can: server.can };
        },
        createProject: async (_orgId: string | null, name: string) => ({
          id: `${await caller()}-${name}`,
          name,
          created_at: "2026-10-01T00:00:00Z",
          organization_scoped: false,
        }),
        llmCheck: async () => ({}),
      };
    },
  };
});

beforeEach(() => {
  access.granted = false;
  auth.isLoaded = true;
  auth.userId = "user_a";
  clerk.organization = null;
  clerk.membership = null;
  server.can = { create: true, delete: true };
  server.listCalls = [];
  server.entitlementCalls = 0;
  server.projects = {
    user_a: [
      {
        id: "a-1",
        name: "A's secret project",
        created_at: "2026-10-01T00:00:00Z",
        organization_scoped: false,
      },
    ],
    user_b: [],
  };
});

describe("workspace", () => {
  it("shows the LLM connectivity check only to a user the API lets use the operator tools", async () => {
    const { unmount } = render(<Workspace />);
    expect(await screen.findByText("A's secret project")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "LLM connectivity" })).toBeNull();
    unmount();

    access.granted = true;
    render(<Workspace />);
    expect(await screen.findByRole("heading", { name: "LLM connectivity" })).toBeInTheDocument();
  });

  it.each([
    ["an administrator the API does not let delete", "org:admin", { create: true, delete: false }, false],
    ["a member the API lets delete", "org:member", { create: true, delete: true }, true],
  ])("shows Delete for %s as the API says, whatever Clerk's role is", async (_case, role, can, shown) => {
    clerk.organization = { id: "org_1", name: "Acme" };
    clerk.membership = { role };
    server.can = can;
    render(<Workspace />);
    expect(await screen.findByRole("heading", { name: "Acme" })).toBeInTheDocument();
    expect(await screen.findByText("A's secret project")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete A's secret project" }) !== null).toBe(shown);
  });

  it("shows nothing of the previous user's after a Clerk account switch", async () => {
    const { rerender } = render(<Workspace />);
    expect(await screen.findByText("A's secret project")).toBeInTheDocument();
    expect(await screen.findByText("Pro")).toBeInTheDocument();
    // User A also creates a project, which the panel keeps in local state.
    await userEvent.type(screen.getByLabelText("New project name"), "A's new project");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByText("A's new project")).toBeInTheDocument();

    // Another account in the same browser; nothing remounts the page.
    auth.userId = "user_b";
    rerender(<Workspace />);
    expect(screen.queryByText("A's secret project")).toBeNull();
    expect(screen.queryByText("A's new project")).toBeNull();
    expect(screen.queryByText("Pro")).toBeNull();

    expect(await screen.findByText("No projects yet")).toBeInTheDocument();
    expect(await screen.findByText("Free")).toBeInTheDocument();
    await waitFor(() => expect(server.listCalls).toEqual(["user_a", "user_b"]));
    expect(screen.queryByText("A's secret project")).toBeNull();
    expect(screen.queryByText("A's new project")).toBeNull();
  });

  it("offers a reload instead of loading forever when Clerk never loads (§16.4)", () => {
    vi.useFakeTimers();
    try {
      auth.isLoaded = false;
      auth.userId = undefined;
      const reloadPage = vi.fn();
      vi.spyOn(window, "location", "get").mockReturnValue({ ...window.location, reload: reloadPage });
      render(<Workspace />);
      expect(screen.getByText("Loading workspace…")).toBeInTheDocument();

      act(() => vi.advanceTimersByTime(CLERK_LOAD_BACKSTOP_MS));
      expect(screen.getByRole("alert")).toHaveTextContent("Sign-in unavailable");
      act(() => screen.getByRole("button", { name: "Try again" }).click());
      expect(reloadPage).toHaveBeenCalledOnce();
      expect(server.entitlementCalls).toBe(0);
    } finally {
      vi.useRealTimers();
      vi.restoreAllMocks();
    }
  });

  it("requests each resource once on a fresh page load", async () => {
    // clerk-js is still loading: no user yet.
    auth.isLoaded = false;
    auth.userId = undefined;
    const { rerender } = render(<Workspace />);
    auth.isLoaded = true;
    auth.userId = "user_a";
    rerender(<Workspace />);
    expect(await screen.findByText("A's secret project")).toBeInTheDocument();
    expect(await screen.findByText("Pro")).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(server.listCalls).toEqual(["user_a"]);
    expect(server.entitlementCalls).toBe(1);
  });
});
