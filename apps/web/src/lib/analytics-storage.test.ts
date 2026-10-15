/**
 * OBS-03 with the real PostHog SDK (not mocked): what it keeps in browser
 * storage. The SDK stores the first-seen URL and each session's entry URL,
 * where before_send cannot reach them, so token query parameters are masked
 * there and fragments dropped. One scenario per file: it needs a fresh SDK
 * instance and storage.
 */
import { installPostHogTransport } from "./posthog-transport.testing";

const TICKET = "CANARYTICKET42";

vi.mock("./env", () => ({
  publicEnv: { analytics: { enabled: true, key: "phc_test", host: "https://eu.i.posthog.com" } },
}));

installPostHogTransport();

/** The URLs PostHog keeps in localStorage, and every stored value as text. */
function stored() {
  const persisted = JSON.parse(localStorage.getItem("ph_phc_test_posthog") ?? "{}") as {
    $initial_person_info?: { u?: string };
    $client_session_props?: { props?: { u?: string } };
  };
  const values = [localStorage, sessionStorage].flatMap((storage) =>
    Object.keys(storage).map((key) => `${key}=${storage.getItem(key) ?? ""}`),
  );
  return {
    firstSeen: persisted.$initial_person_info?.u,
    sessionEntry: persisted.$client_session_props?.props?.u,
    text: [...values, document.cookie].join("\n"),
  };
}

it("masks token query parameters and drops fragments in the URLs it stores", async () => {
  // An invitation link; browsers report a cross-origin referrer as its origin.
  const redirect = encodeURIComponent(`/billing/success?session_id=${TICKET}`);
  window.history.replaceState(
    {},
    "",
    `/sign-up?__clerk_ticket=${TICKET}&__clerk_status=sign_up&redirect_url=${redirect}#${TICKET}`,
  );
  Object.defineProperty(document, "referrer", { configurable: true, value: "https://mail.example.com/" });
  const { identify, resetAnalytics, startAnalytics } = await import("./analytics");

  startAnalytics();
  identify("internal-user-1");
  const landing =
    "http://localhost:3000/sign-up?__clerk_ticket=<masked>&__clerk_status=<masked>&redirect_url=<masked>";
  // Not vacuous: the URLs are stored, with their paths and masked parameters.
  expect(stored()).toMatchObject({ firstSeen: landing, sessionEntry: landing });
  expect(stored().text).not.toContain(TICKET);

  // Signed out on Stripe's return URL: the reset stores both URLs again from there.
  window.history.pushState({}, "", `/billing/success?session_id=${TICKET}#${TICKET}`);
  resetAnalytics();
  identify("internal-user-2");
  const checkoutReturn = "http://localhost:3000/billing/success?session_id=<masked>";
  expect(stored()).toMatchObject({ firstSeen: checkoutReturn, sessionEntry: checkoutReturn });
  expect(stored().text).not.toContain(TICKET);
});
