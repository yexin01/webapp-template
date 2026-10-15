import { act, renderHook, waitFor } from "@testing-library/react";

import { useResource } from "./use-resource";

const auth = vi.hoisted(() => ({ isLoaded: true, userId: "user_a" as string | null | undefined }));
vi.mock("@clerk/nextjs", () => ({ useAuth: () => auth }));

beforeEach(() => {
  auth.isLoaded = true;
  auth.userId = "user_a";
});

describe("useResource", () => {
  it("reports loading for a new key and never returns stale data", async () => {
    const fetchA = vi.fn().mockResolvedValue("A");
    const fetchB = vi.fn(() => new Promise<string>(() => undefined));
    const { result, rerender } = renderHook(({ f, k }) => useResource(f, k), {
      initialProps: { f: fetchA as () => Promise<string>, k: "a" },
    });
    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.data).toBe("A"));
    rerender({ f: fetchB, k: "b" });
    expect(result.current.loading).toBe(true);
    expect(result.current.data).toBeUndefined();
  });

  it("hides and refetches the previous user's data when the signed-in user changes", async () => {
    let answer: Promise<string> = Promise.resolve("A's data");
    const fetcher = vi.fn(() => answer);
    const { result, rerender } = renderHook(() => useResource(fetcher, "same-key"));
    await waitFor(() => expect(result.current.data).toBe("A's data"));

    // Same fetcher and key, as when Clerk switches accounts without a remount.
    let resolveB: (value: string) => void = () => undefined;
    answer = new Promise<string>((resolve) => (resolveB = resolve));
    auth.userId = "user_b";
    rerender();
    expect(result.current.data).toBeUndefined();
    expect(result.current.loading).toBe(true);
    expect(fetcher).toHaveBeenCalledTimes(2);
    act(() => resolveB("B's data"));
    await waitFor(() => expect(result.current.data).toBe("B's data"));
  });

  it("waits for Clerk and fetches once on a fresh page load", async () => {
    // Before clerk-js loads, userId is undefined; the API client is replaced when it arrives.
    auth.isLoaded = false;
    auth.userId = undefined;
    const fetcher = vi.fn().mockResolvedValue("data");
    const { result, rerender } = renderHook(({ f }) => useResource(f, "k"), {
      initialProps: { f: fetcher as () => Promise<string> },
    });
    expect(result.current.loading).toBe(true);
    expect(fetcher).not.toHaveBeenCalled();

    auth.isLoaded = true;
    auth.userId = "user_a";
    const loadedFetcher = vi.fn().mockResolvedValue("data");
    rerender({ f: loadedFetcher });
    await waitFor(() => expect(result.current.data).toBe("data"));
    expect(fetcher).not.toHaveBeenCalled();
    expect(loadedFetcher).toHaveBeenCalledOnce();
  });

  it("exposes errors and reloads on demand", async () => {
    const fetcher = vi.fn().mockRejectedValueOnce(new Error("x")).mockResolvedValueOnce("ok");
    const { result } = renderHook(() => useResource(fetcher, "k"));
    await waitFor(() => expect(result.current.error).toBeInstanceOf(Error));
    act(() => result.current.reload());
    await waitFor(() => expect(result.current.data).toBe("ok"));
  });
});
