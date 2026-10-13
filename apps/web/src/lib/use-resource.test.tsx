import { act, renderHook, waitFor } from "@testing-library/react";

import { useResource } from "./use-resource";

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

  it("exposes errors and reloads on demand", async () => {
    const fetcher = vi.fn().mockRejectedValueOnce(new Error("x")).mockResolvedValueOnce("ok");
    const { result } = renderHook(() => useResource(fetcher, "k"));
    await waitFor(() => expect(result.current.error).toBeInstanceOf(Error));
    act(() => result.current.reload());
    await waitFor(() => expect(result.current.data).toBe("ok"));
  });
});
