import { renderHook } from "@testing-library/react";

import { useActiveTenant } from "./use-active-tenant";

const clerk = vi.hoisted(() => ({
  isLoaded: true,
  organization: null as { id: string; name: string } | null | undefined,
  membership: undefined as { role: string } | null | undefined,
}));
vi.mock("@clerk/nextjs", () => ({ useOrganization: () => clerk }));

beforeEach(() => {
  clerk.isLoaded = true;
  clerk.organization = null;
  clerk.membership = null;
});

describe("useActiveTenant", () => {
  it("is the personal workspace when no organization is active", () => {
    const { result } = renderHook(() => useActiveTenant());
    expect(result.current).toEqual({ isLoaded: true, orgId: null });
  });

  it("names the active organization and nothing else about it", () => {
    clerk.organization = { id: "org_1", name: "Acme" };
    clerk.membership = { role: "org:admin" };
    const { result } = renderHook(() => useActiveTenant());
    expect(result.current).toEqual({ isLoaded: true, orgId: "org_1" });
  });

  it("is not loaded until Clerk has loaded, and names no organization meanwhile", () => {
    clerk.isLoaded = false;
    clerk.organization = undefined;
    clerk.membership = undefined;
    const { result } = renderHook(() => useActiveTenant());
    expect(result.current).toEqual({ isLoaded: false, orgId: null });
  });

  it("follows a switch of the active organization", () => {
    clerk.organization = { id: "org_1", name: "Acme" };
    const { result, rerender } = renderHook(() => useActiveTenant());
    expect(result.current.orgId).toBe("org_1");
    clerk.organization = { id: "org_2", name: "Globex" };
    rerender();
    expect(result.current.orgId).toBe("org_2");
    clerk.organization = null;
    rerender();
    expect(result.current.orgId).toBeNull();
  });
});
