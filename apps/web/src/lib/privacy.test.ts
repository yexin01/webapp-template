import { filterProperties } from "./analytics";
import { redactDeep, redactText, scrubBreadcrumb, scrubEvent, stripQuery } from "./redact";

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

describe("deep redaction for error reports", () => {
  const SECRET = "sk_" + "live_CANARY1234567890";

  class ThrowingToString {
    toString(): string {
      throw new Error("toString failed");
    }
  }
  const unreadable: [string, () => unknown][] = [
    ["an instance whose toString throws", () => new ThrowingToString()],
    // String() throws: no toString anywhere on the prototype chain.
    ["an instance without Object.prototype", () => Object.create(Object.create(null) as object) as object],
    [
      "an object with a throwing getter",
      () =>
        Object.defineProperty({}, "boom", {
          enumerable: true,
          get() {
            throw new Error("getter failed");
          },
        }),
    ],
    [
      "a proxy that cannot list its keys",
      () =>
        new Proxy(
          {},
          {
            ownKeys() {
              throw new Error("ownKeys failed");
            },
          },
        ),
    ],
  ];

  it.each(unreadable)("never throws on %s, and keeps redacting its siblings", (_label, make) => {
    expect(redactDeep(make())).toSatisfy((v: unknown) => JSON.stringify(v).includes("[Unserializable]"));
    const value = { secret: SECRET, odd: make() };
    const redacted = redactDeep(value) as Record<string, unknown>;
    expect(redacted.secret).toBe("[REDACTED]");
    expect(JSON.stringify(redacted.odd)).toContain("[Unserializable]");
    const breadcrumb = scrubBreadcrumb({
      category: "console",
      message: SECRET,
      data: { arguments: [make()] },
    });
    expect(JSON.stringify(breadcrumb)).not.toContain(SECRET);
    const event = scrubEvent({ type: undefined, extra: { odd: make(), secret: SECRET } });
    expect(JSON.stringify(event)).not.toContain(SECRET);
  });

  it("replaces only the property that cannot be read", () => {
    const value = Object.defineProperty({ secret: SECRET }, "boom", {
      enumerable: true,
      get() {
        throw new Error("getter failed");
      },
    });
    expect(redactDeep(value)).toEqual({ secret: "[REDACTED]", boom: "[Unserializable]" });
  });

  it("cuts cycles and repeated references", () => {
    const node: Record<string, unknown> = { secret: SECRET };
    node.self = node;
    const shared = { secret: SECRET };
    const redacted = redactDeep({ node, a: shared, b: shared }) as Record<string, Record<string, unknown>>;
    expect(redacted.node?.self).toBe("[Circular]");
    expect(redacted.a?.secret).toBe("[REDACTED]");
    expect(redacted.b).toBe("[Repeated]");
    // Every key pointing back at the object: without cycle tracking this grows as k^depth.
    const fan: Record<string, unknown> = {};
    for (let i = 0; i < 50; i++) fan[`k${i}`] = fan;
    const started = performance.now();
    expect(JSON.stringify(redactDeep(fan))).not.toContain(SECRET);
    expect(performance.now() - started).toBeLessThan(250);
  });

  it("bounds breadth and total work, and redacts object keys", () => {
    const rows = Array.from({ length: 100_000 }, (_, i) => ({ id: i, email: `person${i}@example.com` }));
    const wide = Object.fromEntries(Array.from({ length: 5_000 }, (_, i) => [`key${i}`, i]));
    const grid = Array.from({ length: 1_000 }, () => Array.from({ length: 1_000 }, (_, i) => ({ i })));
    const started = performance.now();
    const redactedRows = redactDeep(rows) as unknown[];
    const redactedWide = redactDeep(wide) as Record<string, unknown>;
    const redactedGrid = JSON.stringify(redactDeep(grid));
    expect(performance.now() - started).toBeLessThan(1_000);
    expect(redactedRows).toHaveLength(1_001);
    expect(redactedRows.at(-1)).toBe("[99000 more items]");
    expect(JSON.stringify(redactedRows)).not.toContain("@example.com");
    expect(Object.keys(redactedWide)).toHaveLength(1_001);
    expect(redactedGrid).toContain("[Truncated]");
    expect(redactDeep({ [`${SECRET}`]: 1 })).toEqual({ "[REDACTED]": 1 });
  });
});
