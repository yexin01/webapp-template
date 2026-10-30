import type { ErrorEvent } from "@sentry/nextjs";

import { ApiError } from "./api/errors";
import { prepareReport } from "./error-reporting";

vi.mock("@sentry/nextjs", () => ({}));
vi.mock("./env", () => ({ publicEnv: { errorReporting: { enabled: true } } }));

const REQUEST_ID = "0f1e2d3c4b5a69788796a5b4c3d2e1f0";
const EMAIL = "canary.person@example.com";
const report = (): ErrorEvent =>
  ({ type: undefined, tags: { page: "billing" }, message: `Failed for ${EMAIL}` }) as ErrorEvent;

describe("prepareReport (OBS-05)", () => {
  it("tags a report about a failed API call with the API's request ID, and scrubs it", () => {
    const failure = new ApiError(500, "INTERNAL_ERROR", "Something failed.", REQUEST_ID);
    const prepared = prepareReport(report(), { originalException: failure });
    // The API tags its own reports with the same name.
    expect(prepared.tags).toEqual({ page: "billing", request_id: REQUEST_ID });
    expect(JSON.stringify(prepared)).not.toContain(EMAIL);
  });

  it.each([
    ["an API error without a request ID", new ApiError(0, "NETWORK_ERROR", "Offline.", null)],
    [
      "an API error with a request ID of another shape",
      new ApiError(500, "X", "x", `<script>${EMAIL}</script>`),
    ],
    ["another error", new Error("boom")],
    ["no error at all", undefined],
  ])("adds no tag for %s", (_case, failure) => {
    const prepared = prepareReport(report(), { originalException: failure });
    expect(prepared.tags).toEqual({ page: "billing" });
    expect(JSON.stringify(prepared)).not.toContain(EMAIL);
  });

  it("works without a hint", () => {
    expect(prepareReport(report()).tags).toEqual({ page: "billing" });
  });
});
