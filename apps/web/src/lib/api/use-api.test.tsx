import { renderHook } from "@testing-library/react";

import { useApi } from "./use-api";

const auth = vi.hoisted(() => ({ userId: "user_a" as string | null }));
// One stable getToken for the Clerk instance, as in @clerk/nextjs.
const getToken = vi.hoisted(() => async () => "token");
vi.mock("@clerk/nextjs", () => ({ useAuth: () => ({ userId: auth.userId, getToken }) }));

describe("useApi", () => {
  it("keeps one client per signed-in user and replaces it after an account switch", () => {
    auth.userId = "user_a";
    const { result, rerender } = renderHook(() => useApi());
    const first = result.current;
    rerender();
    expect(result.current).toBe(first);

    auth.userId = "user_b";
    rerender();
    expect(result.current).not.toBe(first);
  });
});
