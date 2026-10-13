import { filterProperties } from "./analytics";
import { redactText, stripQuery } from "./redact";

describe("analytics allowlist", () => {
  it("drops properties that are not allowlisted for the event", () => {
    const props = { plan: "pro_monthly", email: "person@example.com" } as unknown as { plan: string };
    expect(filterProperties("upgrade_clicked", props)).toEqual({ plan: "pro_monthly" });
    expect(filterProperties("billing_portal_clicked", props as never)).toEqual({});
  });
});

describe("redaction", () => {
  it.each([
    "sk_" + "live_CANARY1234567890",
    "whsec_CANARY1234567890",
    "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJl",
    "Bearer abcdefghijklmnop",
    "person@example.com",
  ])("removes %s", (secret) => {
    expect(redactText(`value ${secret} end`)).not.toContain(secret);
  });

  it("removes token query parameters and strips URLs", () => {
    expect(redactText("/x?token=abc123&ok=1")).toBe("/x?token=[REDACTED]&ok=1");
    expect(stripQuery("https://a.example/p?session_id=1#frag")).toBe("https://a.example/p");
  });
});
