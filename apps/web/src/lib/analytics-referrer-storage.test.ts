/**
 * OBS-03 with the real PostHog SDK (not mocked): the referrers it keeps in
 * browser storage. A page reached from another page of this site has that
 * page's full URL as its referrer, query included (Referrer-Policy is
 * strict-origin-when-cross-origin), and the SDK stores it unmasked. One
 * scenario per file: it needs a fresh SDK instance and storage.
 */
import posthog from "posthog-js";

import { installPostHogTransport } from "./posthog-transport.testing";

const TICKET = "CANARYTICKET42";

vi.mock("./env", () => ({
  publicEnv: { analytics: { enabled: true, key: "phc_test", host: "https://eu.i.posthog.com" } },
}));

installPostHogTransport();

/** The referrers PostHog keeps in browser storage, and every stored value as text. */
function stored() {
  const persisted = JSON.parse(localStorage.getItem("ph_phc_test_posthog") ?? "{}") as {
    $initial_person_info?: { r?: string };
    $client_session_props?: { props?: { r?: string } };
  };
  const session = JSON.parse(sessionStorage.getItem("ph_phc_test_posthog") ?? "{}") as { $referrer?: string };
  const values = [localStorage, sessionStorage].flatMap((storage) =>
    Object.keys(storage).map((key) => `${key}=${storage.getItem(key) ?? ""}`),
  );
  return {
    firstSeen: persisted.$initial_person_info?.r,
    sessionEntry: persisted.$client_session_props?.props?.r,
    session: session.$referrer,
    text: [...values, document.cookie].join("\n"),
  };
}

it("keeps only the origin and path of the referrers it stores", async () => {
  // Reached from a Checkout return URL by a full page load.
  window.history.replaceState({}, "", "/billing");
  Object.defineProperty(document, "referrer", {
    configurable: true,
    value: `http://localhost:3000/billing/success?session_id=${TICKET}#${TICKET}`,
  });
  const { identify, resetAnalytics, startAnalytics } = await import("./analytics");

  startAnalytics();
  identify("internal-user-1");
  const referrer = "http://localhost:3000/billing/success";
  // Not vacuous: the referrers are stored, with their paths.
  expect(stored()).toMatchObject({ firstSeen: referrer, sessionEntry: referrer, session: referrer });
  expect(stored().text).not.toContain(TICKET);

  // Signed out: the reset clears them. A session can start before the next capture
  // (here when something asks for the session ID), which stores its entry referrer.
  resetAnalytics();
  posthog.get_session_id();
  expect(stored()).toMatchObject({ sessionEntry: referrer });
  expect(stored().text).not.toContain(TICKET);

  // The next capture stores the other two, in the session that has already started.
  identify("internal-user-2");
  expect(stored()).toMatchObject({ firstSeen: referrer, sessionEntry: referrer, session: referrer });
  expect(stored().text).not.toContain(TICKET);
});
