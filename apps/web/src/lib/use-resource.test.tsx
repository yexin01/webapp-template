import { act, renderHook, waitFor } from "@testing-library/react";

import { presentError } from "./api/errors";
import { CLERK_LOAD_BACKSTOP_MS, useResource } from "./use-resource";

const auth = vi.hoisted(() => ({ isLoaded: true, userId: "user_a" as string | null | undefined }));
const clerk = vi.hoisted(() => ({ status: "loading" as "loading" | "ready" | "degraded" | "error" }));
vi.mock("@clerk/nextjs", () => ({ useAuth: () => auth, useClerk: () => clerk }));

beforeEach(() => {
  auth.isLoaded = true;
  auth.userId = "user_a";
  clerk.status = "loading";
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

  it("reports sign-in as unavailable at once when the SDK reports that Clerk failed to load", () => {
    auth.isLoaded = false;
    auth.userId = undefined;
    clerk.status = "error";
    const reloadPage = vi.fn();
    vi.spyOn(window, "location", "get").mockReturnValue({ ...window.location, reload: reloadPage });
    const fetcher = vi.fn().mockResolvedValue("data");
    try {
      const { result } = renderHook(() => useResource(fetcher, "k"));
      expect(result.current).toMatchObject({ loading: false, data: undefined });
      expect(presentError(result.current.error)).toMatchObject({
        title: "Sign-in unavailable",
        action: "retry",
      });
      result.current.reload();
      expect(reloadPage).toHaveBeenCalledOnce();
      expect(fetcher).not.toHaveBeenCalled();
    } finally {
      vi.restoreAllMocks();
    }
  });

  it("loads the data when Clerk is degraded: it has loaded, only its Frontend API is down", async () => {
    clerk.status = "degraded";
    const fetcher = vi.fn().mockResolvedValue("data");
    const { result } = renderHook(() => useResource(fetcher, "k"));
    await waitFor(() => expect(result.current.data).toBe("data"));
    expect(result.current.error).toBeUndefined();
  });

  it("does not report a failure for a Clerk that is still loading", () => {
    auth.isLoaded = false;
    auth.userId = undefined;
    const { result } = renderHook(() => useResource(vi.fn(), "k"));
    expect(result.current).toMatchObject({ loading: true, error: undefined });
  });

  it("reports sign-in as unavailable, with a page reload, when Clerk never loads or fails", () => {
    vi.useFakeTimers();
    try {
      // clerk-js was blocked or its Frontend API is down: Clerk never reports loaded.
      auth.isLoaded = false;
      auth.userId = undefined;
      const reloadPage = vi.fn();
      vi.spyOn(window, "location", "get").mockReturnValue({ ...window.location, reload: reloadPage });
      const fetcher = vi.fn().mockResolvedValue("data");
      const { result } = renderHook(() => useResource(fetcher, "k"));

      act(() => vi.advanceTimersByTime(CLERK_LOAD_BACKSTOP_MS - 1));
      expect(result.current).toMatchObject({ loading: true, error: undefined });
      act(() => vi.advanceTimersByTime(1));
      expect(result.current).toMatchObject({ loading: false, data: undefined });
      expect(presentError(result.current.error)).toMatchObject({
        title: "Sign-in unavailable",
        action: "retry",
      });

      result.current.reload();
      expect(reloadPage).toHaveBeenCalledOnce();
      expect(fetcher).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
      vi.restoreAllMocks();
    }
  });

  it("recovers when Clerk loads after the deadline after all", async () => {
    auth.isLoaded = false;
    auth.userId = undefined;
    const fetcher = vi.fn().mockResolvedValue("data");
    vi.useFakeTimers();
    const { result, rerender } = renderHook(() => useResource(fetcher, "k"));
    try {
      act(() => vi.advanceTimersByTime(CLERK_LOAD_BACKSTOP_MS));
    } finally {
      vi.useRealTimers();
    }
    expect(result.current.error).toBeDefined();

    // A very slow network: the script arrives late, and the data loads without a reload.
    auth.isLoaded = true;
    auth.userId = "user_a";
    rerender();
    await waitFor(() => expect(result.current.data).toBe("data"));
    expect(result.current.error).toBeUndefined();
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it("exposes errors and reloads on demand", async () => {
    const fetcher = vi.fn().mockRejectedValueOnce(new Error("x")).mockResolvedValueOnce("ok");
    const { result } = renderHook(() => useResource(fetcher, "k"));
    await waitFor(() => expect(result.current.error).toBeInstanceOf(Error));
    act(() => result.current.reload());
    await waitFor(() => expect(result.current.data).toBe("ok"));
  });
});
