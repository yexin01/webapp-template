// @vitest-environment node
import { ConfigError, isWebVariable, loadServerEnv } from "./env.server";

// Synthetic, well-formed development keys; neither grants access to anything.
const publishable = `pk_test_${Buffer.from("example-app.clerk.accounts.dev$").toString("base64")}`;
const livePublishable = `pk_live_${Buffer.from("clerk.example.com$").toString("base64")}`;
const base = {
  APP_ENV: "local",
  NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: publishable,
  CLERK_SECRET_KEY: "sk_test_not_a_real_secret",
};
const serving = { serving: true };

const ORIGIN_PROBLEM = "API_ORIGIN must be an http(s) origin, with no path, query, fragment, or credentials";

function problems(env: Record<string, string | undefined>, options = serving): string[] {
  try {
    loadServerEnv(env, options);
    return [];
  } catch (error) {
    if (!(error instanceof ConfigError)) throw error;
    return [...error.problems];
  }
}

describe("web server configuration (ENV-03)", () => {
  it("accepts a minimal local configuration and applies defaults", () => {
    const env = loadServerEnv(base, serving);
    expect(env).toMatchObject({
      appEnv: "local",
      apiOrigin: "http://127.0.0.1:8000",
      analytics: { mode: "disabled", enabled: false, host: "https://us.i.posthog.com" },
      errorReporting: { mode: "disabled", enabled: false, tracesSampleRate: 0 },
    });
    expect(Object.isFrozen(env)).toBe(true);
  });

  it.each([
    [
      { NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: "pk_test_replace_me" },
      "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is malformed",
    ],
    [{ NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: "not-a-key" }, "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is malformed"],
    [{ NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: "" }, "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is required"],
    [
      { CLERK_SECRET_KEY: "sk_test_replace_me" },
      "CLERK_SECRET_KEY is malformed or still the .env.example placeholder",
    ],
    [{ CLERK_SECRET_KEY: undefined }, "CLERK_SECRET_KEY is required"],
    [{ CLERK_JWT_KEY: "not a pem" }, "CLERK_JWT_KEY must be a PEM-encoded public key"],
    [{ APP_ENV: "staging" }, "APP_ENV must be local, test, preview, or production"],
    [{ API_ORIGIN: "127.0.0.1:8000" }, ORIGIN_PROBLEM],
    [{ API_ORIGIN: "ftp://api.example.com" }, ORIGIN_PROBLEM],
    [{ API_ORIGIN: "https://api.example.com//" }, ORIGIN_PROBLEM],
    [{ API_ORIGIN: "https://api.example.com/v2" }, ORIGIN_PROBLEM],
    [{ API_ORIGIN: "https://api.example.com/api/v1/" }, ORIGIN_PROBLEM],
    [{ API_ORIGIN: "https://api.example.com?x=1" }, ORIGIN_PROBLEM],
    [{ API_ORIGIN: "https://api.example.com/?" }, ORIGIN_PROBLEM],
    [{ API_ORIGIN: "https://api.example.com#frag" }, ORIGIN_PROBLEM],
    [{ API_ORIGIN: "https://user:pass@api.example.com" }, ORIGIN_PROBLEM],
    [{ API_ORIGIN: "https://token@api.example.com" }, ORIGIN_PROBLEM],
    [{ ANALYTICS_MODE: "live" }, "ANALYTICS_MODE=live is only allowed when APP_ENV=production"],
    [{ ERROR_REPORTING_MODE: "live" }, "ERROR_REPORTING_MODE=live is only allowed when APP_ENV=production"],
    [{ ANALYTICS_MODE: "mock" }, "ANALYTICS_MODE must be disabled, sandbox, or live"],
    [{ ANALYTICS_MODE: "sandbox" }, "NEXT_PUBLIC_POSTHOG_KEY is required when ANALYTICS_MODE is enabled"],
    [
      { NEXT_PUBLIC_POSTHOG_HOST: "eu.i.posthog.com" },
      "NEXT_PUBLIC_POSTHOG_HOST must be an absolute http(s) URL",
    ],
    [
      { ERROR_REPORTING_MODE: "sandbox" },
      "NEXT_PUBLIC_SENTRY_DSN is required when ERROR_REPORTING_MODE is enabled",
    ],
    [
      { NEXT_PUBLIC_SENTRY_DSN: "https://o1.ingest.sentry.io/2" },
      "NEXT_PUBLIC_SENTRY_DSN must be a Sentry DSN (https://<key>@<host>/<id>)",
    ],
    [{ SENTRY_TRACES_SAMPLE_RATE: "abc" }, "SENTRY_TRACES_SAMPLE_RATE must be a number from 0 to 1"],
    [{ SENTRY_TRACES_SAMPLE_RATE: "1.5" }, "SENTRY_TRACES_SAMPLE_RATE must be a number from 0 to 1"],
    [{ SENTRY_TRACES_SAMPLE_RATE: "-0.1" }, "SENTRY_TRACES_SAMPLE_RATE must be a number from 0 to 1"],
    [
      { NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: livePublishable },
      "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is a live key; live keys are only allowed when APP_ENV=production",
    ],
  ])("rejects %o", (patch, message) => {
    expect(problems({ ...base, ...patch })).toContain(message);
  });

  it.each([
    ["https://api.example.com", "https://api.example.com"],
    ["https://api.example.com/", "https://api.example.com"],
    ["http://127.0.0.1:8100", "http://127.0.0.1:8100"],
    ["HTTPS://API.Example.com:443/", "https://api.example.com"],
  ])("accepts the API origin %s as %s", (value, origin) => {
    expect(loadServerEnv({ ...base, API_ORIGIN: value }, serving).apiOrigin).toBe(origin);
  });

  describe("API_ORIGIN in production", () => {
    // Every request's session token is forwarded to this origin by the /api/v1 rewrite.
    const production = {
      APP_ENV: "production",
      NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: livePublishable,
      CLERK_SECRET_KEY: "sk_live_not_a_real_secret",
    };

    it.each([
      "http://api.example.com",
      "http://10.0.0.5:8000",
      "http://127.evil.example",
      "http://localhost.:8000",
    ])("refuses plain http to %s", (origin) => {
      expect(problems({ ...production, API_ORIGIN: origin })).toEqual([
        "API_ORIGIN must use https in production",
      ]);
    });

    it.each([
      ["https://api.example.com", "https://api.example.com"],
      ["http://127.0.0.1:8000", "http://127.0.0.1:8000"],
      ["http://127.1:8000", "http://127.0.0.1:8000"],
      ["http://localhost:8000", "http://localhost:8000"],
      ["http://[::1]:8000", "http://[::1]:8000"],
      [undefined, "http://127.0.0.1:8000"],
    ])("accepts %s, as %s", (origin, expected) => {
      expect(loadServerEnv({ ...production, API_ORIGIN: origin }, serving).apiOrigin).toBe(expected);
    });

    it.each(["preview", "local"])("still accepts plain http when APP_ENV=%s", (appEnv) => {
      expect(problems({ ...base, APP_ENV: appEnv, API_ORIGIN: "http://api.example.com" })).toEqual([]);
    });
  });

  it("names variables but never echoes their values", () => {
    const secret = "sk_live_" + "CANARY0123456789abcdef";
    const env = {
      ...base,
      CLERK_SECRET_KEY: secret,
      ANALYTICS_MODE: "phc_CANARYpasted_into_the_wrong_field",
      SENTRY_TRACES_SAMPLE_RATE: "CANARY",
    };
    const error = (() => {
      try {
        loadServerEnv(env, serving);
      } catch (caught) {
        return caught as ConfigError;
      }
    })();
    expect(error).toBeInstanceOf(ConfigError);
    expect(error?.message).toContain("CLERK_SECRET_KEY");
    expect(error?.message).toContain("ANALYTICS_MODE");
    expect(error?.message).not.toContain("CANARY");
  });

  it("requires the Clerk secret only when serving, as for the account-free CI build", () => {
    const build = { NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: publishable };
    expect(problems(build, { serving: false })).toEqual([]);
    expect(problems(build, { serving: true })).toEqual(["CLERK_SECRET_KEY is required"]);
  });

  it("needs no Clerk keys under APP_ENV=test but still refuses live keys there", () => {
    expect(problems({ APP_ENV: "test" })).toEqual([]);
    expect(problems({ APP_ENV: "test", CLERK_SECRET_KEY: "sk_live_x" })).toEqual([
      "CLERK_SECRET_KEY is a live key; live keys are only allowed when APP_ENV=production",
    ]);
  });

  it("requires live keys and no sandbox telemetry in production (ENV-02, ENV-04)", () => {
    const production = {
      ...base,
      APP_ENV: "production",
      ANALYTICS_MODE: "sandbox",
      NEXT_PUBLIC_POSTHOG_KEY: "phc_x",
    };
    expect(problems(production)).toEqual([
      "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY must be a live key when APP_ENV=production",
      "CLERK_SECRET_KEY must be a live key when APP_ENV=production",
      "ANALYTICS_MODE=sandbox is not allowed when APP_ENV=production",
    ]);
    const live = loadServerEnv(
      {
        APP_ENV: "production",
        NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: livePublishable,
        CLERK_SECRET_KEY: "sk_live_not_a_real_secret",
        ANALYTICS_MODE: "live",
        NEXT_PUBLIC_POSTHOG_KEY: "phc_x",
        ERROR_REPORTING_MODE: "live",
        NEXT_PUBLIC_SENTRY_DSN: "https://publickey@o1.ingest.sentry.io/2",
        SENTRY_TRACES_SAMPLE_RATE: "0.25",
      },
      serving,
    );
    expect(live.analytics.enabled).toBe(true);
    expect(live.errorReporting).toMatchObject({ enabled: true, tracesSampleRate: 0.25 });
  });

  describe("STRIPE_CUSTOM_DOMAIN", () => {
    it.each([
      "https://payments.example.com",
      "payments.example.com/x",
      "payments",
      "Payments.Example.com",
      "payments.example.com:8443",
    ])("rejects %s", (value) => {
      expect(problems({ ...base, STRIPE_CUSTOM_DOMAIN: value })).toEqual([
        "STRIPE_CUSTOM_DOMAIN must be a hostname such as payments.example.com",
      ]);
    });

    it("accepts a hostname, and is unset by default or when empty", () => {
      expect(loadServerEnv({ ...base, STRIPE_CUSTOM_DOMAIN: "payments.example.com" }, serving)).toMatchObject(
        {
          stripeCustomDomain: "payments.example.com",
        },
      );
      expect(loadServerEnv(base, serving).stripeCustomDomain).toBeNull();
      expect(loadServerEnv({ ...base, STRIPE_CUSTOM_DOMAIN: " " }, serving).stripeCustomDomain).toBeNull();
    });

    it("is read from the repository .env by the web app", () => {
      expect(isWebVariable("STRIPE_CUSTOM_DOMAIN")).toBe(true);
      expect(isWebVariable("STRIPE_SECRET_KEY")).toBe(false);
    });
  });

  it("treats an empty value as unset", () => {
    expect(
      loadServerEnv({ ...base, ANALYTICS_MODE: "", SENTRY_TRACES_SAMPLE_RATE: " " }, serving),
    ).toMatchObject({
      analytics: { mode: "disabled" },
      errorReporting: { tracesSampleRate: 0 },
    });
  });

  it("lets the web process load only its own variables from the shared .env", () => {
    for (const name of [
      "APP_ENV",
      "API_ORIGIN",
      "CLERK_SECRET_KEY",
      "NEXT_PUBLIC_POSTHOG_KEY",
      "ANALYTICS_MODE",
    ]) {
      expect(isWebVariable(name)).toBe(true);
    }
    for (const name of [
      "STRIPE_SECRET_KEY",
      "STRIPE_WEBHOOK_SECRET",
      "DATABASE_URL",
      "LLM_API_KEY",
      "CRON_SECRET",
      "CLERK_WEBHOOK_SIGNING_SECRET",
      "SENTRY_DSN",
      "SENTRY_AUTH_TOKEN",
    ]) {
      expect(isWebVariable(name)).toBe(false);
    }
  });
});
