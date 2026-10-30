import { filterProperties } from "./analytics";
import {
  redactDeep,
  redactText,
  scrubBreadcrumb,
  scrubEvent,
  scrubSpan,
  SENSITIVE_QUERY_PARAMETERS,
  stripQuery,
} from "./redact";

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

  it.each([...SENSITIVE_QUERY_PARAMETERS, "__clerk_unknown_future_param"])(
    "removes the value of the %s query parameter",
    (name) => {
      expect(redactText(`/p?a=1&${name}=CANARY&b=2`)).toBe(`/p?a=1&${name}=[REDACTED]&b=2`);
    },
  );

  // apps/api/app/kernel/infrastructure/redaction.py
  it.each([
    ...["token", "access_token", "id_token", "code", "session_id", "key", "api_key", "secret", "signature"],
    ...["ticket", "redirect_url", "__clerk_ticket"],
  ])("removes the value of the %s query parameter, as the API does", (name) => {
    expect(redactText(`/p?a=1&${name}=CANARY&b=2`)).toBe(`/p?a=1&${name}=[REDACTED]&b=2`);
  });

  it.each([
    ["after a hyphen", "id-", "id-"],
    ["after a dot", "ref.abc.", "ref.abc."],
    ["from the first eyJ of a longer run", "abc-eyJxxxx-", "abc-"],
  ])("removes a JWT %s", (_label, prefix, kept) => {
    const jwt = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJl";
    expect(redactText(`${prefix}${jwt} end`)).toBe(`${kept}[REDACTED] end`);
  });

  it("removes an email address whose local part runs on past 64 characters", () => {
    const text = `x ${"a".repeat(100)}.jane.doe@example.com y`;
    expect(redactText(text)).not.toMatch(/jane|@example\.com/);
  });

  it.each([100, 300])("removes the whole of an email address with a %i-character local part", (length) => {
    const local = `jane.doe${"a".repeat(length - "jane.doe".length)}`;
    expect(redactText(`x ${local}@example.com y`)).toBe("x [REDACTED] y");
  });

  it.each(["+", "-", "_", "%", "7"])(
    "removes an email address written right after another one, joined by %s",
    (joiner) => {
      expect(redactText(`x to=jane@example.com${joiner}bob@example.org y`)).not.toMatch(/jane|bob|example/);
    },
  );

  it("removes what the unbounded email pattern removed, on 20,000 generated strings", () => {
    const unbounded = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g;
    const fragments = [
      "ab",
      "c",
      ".",
      "@",
      "+",
      "-",
      "1",
      " ",
      "x.yz",
      "@q.rs",
      ".tu",
      "_",
      "%",
      ".v",
      ",",
      "@a",
      "b.cd",
    ];
    let seed = 1; // Park–Miller, so a failing string can be reproduced
    const pick = (count: number) => {
      seed = (seed * 48_271) % 2_147_483_647;
      return seed % count;
    };
    for (let i = 0; i < 20_000; i++) {
      const text = Array.from({ length: 1 + pick(12) }, () => fragments[pick(fragments.length)]).join("");
      // Addresses written one after another, which it matched one by one, are now one match.
      const expected = text.replace(unbounded, "[REDACTED]").replace(/(?:\[REDACTED\])+/g, "[REDACTED]");
      expect(redactText(text), text).toBe(expected);
    }
  });
});

describe("redaction cost on long strings (OBS-06)", () => {
  const MB = 1_000_000;
  const SECRET = "sk_" + "live_CANARY1234567890";
  const timed = <T>(run: () => T): [T, number] => {
    const started = performance.now();
    const result = run();
    return [result, performance.now() - started];
  };

  it.each([
    ["a run of email characters", "a".repeat(50_000)],
    ["a run of JWT-like segments", "eyJ-".repeat(12_500)],
    ["a long local part before a dotted domain", `${"a".repeat(25_000)}@${"b.".repeat(12_500)}`],
  ])("takes linear time on %s, even without the length cap", (_label, text) => {
    const [, ms] = timed(() => redactText(text, Infinity));
    expect(ms).toBeLessThan(500);
  });

  // Long enough that a quadratic pattern takes seconds, so it fails; on 1 MB it would stall the run for an hour.
  const RUN = 84_000;
  const unchanged = (text: string): [string, string] => [text, text];
  it.each<[string, string, string]>([
    ["local-part characters", ...unchanged("a.%+-_".repeat(RUN / 6))],
    ["local-part characters before an @", ...unchanged(`${"a".repeat(RUN)}@`)],
    ["local-part characters before an address", `${"a".repeat(RUN)}@example.com`, "[REDACTED]"],
    ["@ between local-part characters", ...unchanged("a@".repeat(RUN / 2))],
    ["@", ...unchanged("@".repeat(RUN))],
    ["dots after an @", ...unchanged(`a@${".".repeat(RUN)}`)],
    ["dotted labels after an @", ...unchanged(`a@${"b.".repeat(RUN / 2)}`)],
    ["addresses written one after another", `a@b.cc${".d@e.ff".repeat(RUN / 7)}`, "[REDACTED]"],
    ["near-addresses after one", `a@b.cc${".d@e".repeat(RUN / 4)}`, `[REDACTED]${".d@e".repeat(RUN / 4)}`],
  ])(
    "redacts email addresses in linear time on a run of %s, even without the length cap",
    (_label, text, expected) => {
      const [redacted, ms] = timed(() => redactText(text, Infinity));
      expect(ms).toBeLessThan(500);
      expect(redacted).toBe(expected);
    },
  );

  it("keeps the first 4096 characters, without a token cut in half", () => {
    const filler = "word ".repeat(819); // 4095 characters
    const redacted = redactText(`${filler}x${SECRET} ${SECRET}`);
    expect(redacted).toBe(`${filler}[truncated]`);
    expect(redactText(`${filler}x`)).toBe(`${filler}x`);
  });

  it("strips URLs with 2 MB of separators in well under 100 ms", () => {
    const base = "https://app.example.com/billing";
    const urls = ["#", "?", "?#"].map(
      (separator) => `${base}${separator.repeat((2 * MB) / separator.length)}`,
    );
    const [stripped, ms] = timed(() => urls.map(stripQuery));
    expect(ms).toBeLessThan(100);
    expect(stripped).toEqual([base, base, base]);
  });

  it("scrubs a navigation breadcrumb with a 2 MB fragment in well under 100 ms", () => {
    const breadcrumb = {
      category: "navigation",
      data: { from: `/dashboard#${"a".repeat(2 * MB)}`, to: "/b" },
    };
    const [scrubbed, ms] = timed(() => scrubBreadcrumb(breadcrumb));
    expect(ms).toBeLessThan(100);
    expect(scrubbed?.data).toEqual({ from: "/dashboard", to: "/b" });
  });

  it("scrubs a click breadcrumb with a 2 MB attribute value in well under 100 ms", () => {
    const message = `li.list-item > button.button[aria-label="${'Jane"]['.repeat((2 * MB) / 8)}"][type="button"]`;
    const [scrubbed, ms] = timed(() => scrubBreadcrumb({ category: "ui.click", message }));
    expect(ms).toBeLessThan(100);
    expect(scrubbed?.message).toBe("li.list-item > button.button");
  });

  it("scrubs a 1 MB console message and argument in well under 100 ms", () => {
    const line = `Failed to proxy http://127.0.0.1:8000/api/v1/x?token=${"a".repeat(MB)}`;
    const breadcrumb = {
      category: "console",
      message: line,
      data: { arguments: [line, `${SECRET} ${"b".repeat(MB)}`] },
    };
    const [scrubbed, ms] = timed(() => scrubBreadcrumb(breadcrumb));
    expect(ms).toBeLessThan(100);
    expect(scrubbed?.message).toBe("Failed to proxy http://127.0.0.1:8000/api/v1/x?[truncated]");
    expect(JSON.stringify(scrubbed)).not.toContain(SECRET);
  });

  it("scrubs an event with 1 MB exception, extra, and page URL values in well under 100 ms", () => {
    const event = {
      type: undefined,
      exception: { values: [{ type: "Error", value: "eyJ-".repeat(MB / 4) }] },
      extra: { blob: `${SECRET} ${"a".repeat(MB)}` },
      request: { url: `https://app.example.com/billing#${"#".repeat(2 * MB)}` },
    };
    const [scrubbed, ms] = timed(() => scrubEvent(event));
    expect(ms).toBeLessThan(100);
    expect(scrubbed.exception?.values?.[0]?.value?.length).toBeLessThanOrEqual(4096 + "[truncated]".length);
    expect(scrubbed.extra).toEqual({ blob: "[REDACTED] [truncated]" });
    expect(scrubbed.request?.url).toBe("https://app.example.com/billing");
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

describe("span scrubbing", () => {
  const span = (attributes: Record<string, unknown>) => ({
    trace_id: "0123456789abcdef0123456789abcdef",
    span_id: "0123456789abcdef",
    start_timestamp: 1,
    status: "ok" as const,
    is_segment: true,
    name: "GET /sign-up?__clerk_ticket=CANARY",
    attributes,
  });

  it("drops what it cannot read rather than letting the SDK send the span unmodified", () => {
    const unreadable = Object.defineProperty({ "http.target": "/sign-up?__clerk_ticket=CANARY" }, "boom", {
      enumerable: true,
      get() {
        throw new Error("getter failed");
      },
    });
    const scrubbed = scrubSpan(span(unreadable));
    expect(JSON.stringify(scrubbed)).not.toContain("CANARY");
    expect(scrubbed).toMatchObject({ name: "[Unserializable]", attributes: {}, links: [] });
  });

  it("leaves text that is not a path or URL alone, apart from redaction", () => {
    const scrubbed = scrubSpan(span({ "db.system": "postgresql", note: "Is it done? yes", n: 3, ok: true }));
    expect(scrubbed.attributes).toEqual({
      "db.system": "postgresql",
      note: "Is it done? yes",
      n: 3,
      ok: true,
    });
  });
});
