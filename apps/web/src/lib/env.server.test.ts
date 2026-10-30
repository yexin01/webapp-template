// @vitest-environment node
import {
  ConfigError,
  isWebVariable,
  loadServerEnv,
  sentryBuildOptions,
  sourceMapUploadProblems,
} from "./env.server";

// Synthetic, well-formed development keys; neither grants access to anything.
const publishable = `pk_test_${Buffer.from("example-app.clerk.accounts.dev$").toString("base64")}`;
const livePublishable = `pk_live_${Buffer.from("clerk.example.com$").toString("base64")}`;
const base = {
  APP_ENV: "local",
  NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: publishable,
  CLERK_SECRET_KEY: "sk_test_not_a_real_secret",
};
const serving = { serving: true };
/** What a preview or production deployment names explicitly (ENV-03): both telemetry modes, and the origin it is served from. */
const NAMED = {
  ANALYTICS_MODE: "disabled",
  ERROR_REPORTING_MODE: "disabled",
  APP_BASE_URL: "https://app.example.com",
};

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
      "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is still the .env.example placeholder (copy it from Clerk Dashboard > API keys)",
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
      ...NAMED,
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
      expect(problems({ ...base, ...NAMED, APP_ENV: appEnv, API_ORIGIN: "http://api.example.com" })).toEqual(
        [],
      );
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
      ...NAMED,
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
        APP_BASE_URL: NAMED.APP_BASE_URL,
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
      "SENTRY_ORG",
      "SENTRY_PROJECT",
      "VERCEL",
    ]) {
      expect(isWebVariable(name)).toBe(false);
    }
  });
});

describe("the platform's environment (ENV-01)", () => {
  const withKeys = {
    NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: publishable,
    CLERK_SECRET_KEY: "sk_test_not_a_real_secret",
    ...NAMED,
  };

  it.each([
    [
      { APP_ENV: "production", VERCEL_ENV: "preview" },
      "APP_ENV=production is not allowed when VERCEL_ENV=preview",
    ],
    [
      { APP_ENV: "production", VERCEL_ENV: "development" },
      "APP_ENV=production is not allowed when VERCEL_ENV=development",
    ],
    [
      { APP_ENV: "local", VERCEL_ENV: "preview" },
      "APP_ENV=local is not allowed when VERCEL_ENV=preview (set preview)",
    ],
    [
      { APP_ENV: "test", VERCEL_ENV: "preview" },
      "APP_ENV=test is not allowed when VERCEL_ENV=preview (set preview)",
    ],
    [
      { APP_ENV: "local", VERCEL_ENV: "production" },
      "APP_ENV=local is not allowed when VERCEL_ENV=production (set preview or production)",
    ],
    [
      { APP_ENV: "test", VERCEL_ENV: "production" },
      "APP_ENV=test is not allowed when VERCEL_ENV=production (set preview or production)",
    ],
  ])("refuses %o", (platform, message) => {
    expect(problems({ ...withKeys, ...platform })).toContain(message);
  });

  it.each([
    [{ APP_ENV: "preview", VERCEL_ENV: "preview" }, "preview"],
    [{ APP_ENV: "preview", VERCEL_ENV: "production" }, "preview"], // a first deployment, or a staging project
    [{ APP_ENV: "preview", VERCEL_ENV: "development" }, "preview"],
    [{ APP_ENV: "local", VERCEL_ENV: "development" }, "local"],
    [{ APP_ENV: "test", VERCEL_ENV: "development" }, "test"],
    [{ APP_ENV: "local" }, "local"],
    [{ APP_ENV: "test" }, "test"],
    [{ APP_ENV: "preview" }, "preview"],
    [{}, "local"],
  ])("accepts %o as %s", (platform, appEnv) => {
    expect(loadServerEnv({ ...withKeys, ...platform }, serving).appEnv).toBe(appEnv);
  });

  it("accepts production settings on a production deployment", () => {
    const production = {
      APP_ENV: "production",
      VERCEL_ENV: "production",
      NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: livePublishable,
      CLERK_SECRET_KEY: "sk_live_not_a_real_secret",
      ...NAMED,
    };
    expect(loadServerEnv(production, serving).appEnv).toBe("production");
  });

  it("takes an unset APP_ENV from the platform, so the deployment is tagged with its own environment", () => {
    expect(loadServerEnv({ ...withKeys, VERCEL: "1", VERCEL_ENV: "preview" }, serving).appEnv).toBe(
      "preview",
    );
    expect(loadServerEnv({ ...withKeys, VERCEL: "1", VERCEL_ENV: "development" }, serving).appEnv).toBe(
      "local",
    );
    const production = {
      VERCEL: "1",
      VERCEL_ENV: "production",
      NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: livePublishable,
      CLERK_SECRET_KEY: "sk_live_not_a_real_secret",
      ...NAMED,
    };
    expect(loadServerEnv(production, serving).appEnv).toBe("production");
    expect(problems({ ...production, NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: publishable })).toEqual([
      "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY must be a live key when APP_ENV=production",
    ]);
  });

  it("lets an explicit APP_ENV win over the derived one where the platform allows it", () => {
    expect(loadServerEnv({ ...withKeys, APP_ENV: "preview", VERCEL_ENV: "production" }, serving).appEnv).toBe(
      "preview",
    );
  });

  it("treats an empty APP_ENV as unset", () => {
    expect(loadServerEnv({ ...withKeys, APP_ENV: " ", VERCEL_ENV: "preview" }, serving).appEnv).toBe(
      "preview",
    );
  });

  it("refuses an unknown VERCEL_ENV by name, without echoing it", () => {
    const found = problems({ ...withKeys, VERCEL_ENV: "CANARY" });
    expect(found).toEqual(["VERCEL_ENV must be production, preview, or development"]);
  });

  it("reads VERCEL_ENV from the root .env like the API does", () => {
    expect(isWebVariable("VERCEL_ENV")).toBe(true);
  });
});

describe("a Vercel build or deployment (VERCEL=1)", () => {
  const vercelKeys = {
    VERCEL: "1",
    NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: publishable,
    CLERK_SECRET_KEY: "sk_test_not_a_real_secret",
    ...NAMED,
  };

  it("requires an explicit APP_ENV where nothing derives it, instead of tagging the deployment local", () => {
    expect(problems(vercelKeys, { serving: false })).toEqual([
      "APP_ENV is required on Vercel (set preview or production)",
    ]);
    expect(problems({ ...vercelKeys, APP_ENV: "preview" }, { serving: false })).toEqual([]);
    expect(problems({ ...vercelKeys, VERCEL_ENV: "preview" }, { serving: false })).toEqual([]);
  });

  it("requires CLERK_SECRET_KEY at build time, because Vercel serves the build output", () => {
    expect(
      problems({ ...vercelKeys, CLERK_SECRET_KEY: undefined, APP_ENV: "preview" }, { serving: false }),
    ).toEqual(["CLERK_SECRET_KEY is required"]);
  });

  it("leaves the account-free build alone, which sets no VERCEL variable", () => {
    const build = { NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: publishable };
    expect(problems(build, { serving: false })).toEqual([]);
    expect(loadServerEnv(build, { serving: false }).appEnv).toBe("local");
  });

  it.each(["0", "true", ""])("takes VERCEL=%j for no Vercel build", (value) => {
    expect(
      problems({ NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: publishable, VERCEL: value }, { serving: false }),
    ).toEqual([]);
  });

  it("accepts what the DEC-001 local proof builds: VERCEL=1 with its own .env", () => {
    // `vercel build` sets VERCEL=1 and no VERCEL_ENV; the proof's .env sets APP_ENV=local and both Clerk keys.
    expect(problems({ ...vercelKeys, APP_ENV: "local" }, { serving: false })).toEqual([]);
    // `vercel dev -L` sets VERCEL=1 and VERCEL_ENV=development.
    expect(problems({ ...vercelKeys, APP_ENV: "local", VERCEL_ENV: "development" }, serving)).toEqual([]);
  });
});

describe("explicit integration modes in a deployment (ENV-03)", () => {
  const deployed = (appEnv: string) => ({
    APP_ENV: appEnv,
    APP_BASE_URL: NAMED.APP_BASE_URL,
    NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: appEnv === "production" ? livePublishable : publishable,
    CLERK_SECRET_KEY: appEnv === "production" ? "sk_live_not_a_real_secret" : "sk_test_not_a_real_secret",
  });

  it.each(["preview", "production"])("names both telemetry modes when APP_ENV=%s", (appEnv) => {
    expect(problems(deployed(appEnv))).toEqual([
      `ANALYTICS_MODE is required when APP_ENV=${appEnv}`,
      `ERROR_REPORTING_MODE is required when APP_ENV=${appEnv}`,
    ]);
    expect(problems({ ...deployed(appEnv), ...NAMED })).toEqual([]);
    expect(problems({ ...deployed(appEnv), ANALYTICS_MODE: " ", ERROR_REPORTING_MODE: "" })).toHaveLength(2);
  });

  it.each(["local", "test"])("keeps the defaults for APP_ENV=%s", (appEnv) => {
    expect(problems({ ...base, APP_ENV: appEnv })).toEqual([]);
  });
});

describe("PostHog's public key", () => {
  const SECRET_KEYS = ["phx", "phs", "pha", "phr"].map((prefix) => `${prefix}_CANARY0123456789abcdef`);

  it.each(["disabled", "sandbox"])("refuses a secret PostHog credential with ANALYTICS_MODE=%s", (mode) => {
    for (const key of SECRET_KEYS) {
      const found = problems({ ...base, ANALYTICS_MODE: mode, NEXT_PUBLIC_POSTHOG_KEY: key });
      expect(found).toEqual([
        "NEXT_PUBLIC_POSTHOG_KEY holds a secret PostHog credential (personal, secret or OAuth key); use the project token (phc_...) from Project settings",
      ]);
      expect(found.join()).not.toContain("CANARY");
    }
  });

  it("never echoes the value", () => {
    let message = "";
    try {
      loadServerEnv({ ...base, NEXT_PUBLIC_POSTHOG_KEY: SECRET_KEYS[0] }, serving);
    } catch (error) {
      message = (error as ConfigError).message;
    }
    expect(message).toContain("NEXT_PUBLIC_POSTHOG_KEY");
    expect(message).not.toContain("CANARY");
  });

  it.each(["token", "phc", "phc_ x", "pk_live_x", "sk_test_x"])("requires a project token, not %j", (key) => {
    expect(problems({ ...base, NEXT_PUBLIC_POSTHOG_KEY: key })).toEqual([
      "NEXT_PUBLIC_POSTHOG_KEY must be a PostHog project token (phc_...)",
    ]);
  });

  it("accepts a project token, and an unset or empty key", () => {
    expect(problems({ ...base, ANALYTICS_MODE: "sandbox", NEXT_PUBLIC_POSTHOG_KEY: "phc_x" })).toEqual([]);
    expect(problems({ ...base, NEXT_PUBLIC_POSTHOG_KEY: "phc_x" })).toEqual([]);
    expect(problems({ ...base, NEXT_PUBLIC_POSTHOG_KEY: "" })).toEqual([]);
  });
});

describe("source-map upload to Sentry", () => {
  const TOKEN = "CANARYtoken0123456789";
  const ORG_TOKEN = `sntrys_${TOKEN}`;

  it("needs a project when a token is set, and names it without echoing the token", () => {
    const found = sourceMapUploadProblems({ SENTRY_AUTH_TOKEN: TOKEN, SENTRY_ORG: "acme" });
    expect(found).toEqual([
      "SENTRY_PROJECT (the web project's slug) is required when SENTRY_AUTH_TOKEN is set",
    ]);
    expect(found.join()).not.toContain(TOKEN);
  });

  it("needs an organization unless the token is an organization token", () => {
    expect(sourceMapUploadProblems({ SENTRY_AUTH_TOKEN: TOKEN, SENTRY_PROJECT: "web" })).toEqual([
      "SENTRY_ORG is required unless SENTRY_AUTH_TOKEN is an organization token (sntrys_...)",
    ]);
    expect(
      sourceMapUploadProblems({ SENTRY_AUTH_TOKEN: TOKEN, SENTRY_PROJECT: "web", SENTRY_ORG: "acme" }),
    ).toEqual([]);
  });

  it("accepts an organization token with a project", () => {
    expect(sourceMapUploadProblems({ SENTRY_AUTH_TOKEN: ORG_TOKEN, SENTRY_PROJECT: "web" })).toEqual([]);
    expect(sourceMapUploadProblems({ SENTRY_AUTH_TOKEN: ORG_TOKEN })).toEqual([
      "SENTRY_PROJECT (the web project's slug) is required when SENTRY_AUTH_TOKEN is set",
    ]);
  });

  it("asks for nothing without a token, whatever else is set", () => {
    expect(sourceMapUploadProblems({})).toEqual([]);
    expect(sourceMapUploadProblems({ SENTRY_AUTH_TOKEN: " ", SENTRY_PROJECT: "web" })).toEqual([]);
    expect(sourceMapUploadProblems({ SENTRY_ORG: "acme", SENTRY_PROJECT: "web" })).toEqual([]);
  });

  it("keeps the plugin's own warnings and upload errors in the build log, but only when an upload is configured", () => {
    expect(sentryBuildOptions({ SENTRY_AUTH_TOKEN: TOKEN })).toEqual({
      silent: false,
      telemetry: false,
      sourcemaps: { disable: false },
    });
    for (const env of [
      {},
      { SENTRY_AUTH_TOKEN: "" },
      { SENTRY_AUTH_TOKEN: " " },
      { SENTRY_PROJECT: "web" },
    ]) {
      expect(sentryBuildOptions(env)).toEqual({
        silent: true,
        telemetry: false,
        sourcemaps: { disable: true },
      });
    }
  });
});

describe("the origins sessions are accepted from (ENV-03, SEC-14)", () => {
  const PREVIEW = {
    ...base,
    ...NAMED,
    APP_ENV: "preview",
    APP_BASE_URL: "https://preview.example.com",
  };
  const PLATFORM = {
    VERCEL_ENV: "preview",
    VERCEL_URL: "my-app-abc123-team.vercel.app",
    VERCEL_BRANCH_URL: "my-app-git-feature-team.vercel.app",
  };
  const parties = (env: Record<string, string | undefined>) =>
    loadServerEnv(env, serving).clerkAuthorizedParties;

  it("reads both variables from the repository .env, and the platform's URLs only from the process", () => {
    expect(isWebVariable("APP_BASE_URL")).toBe(true);
    expect(isWebVariable("CLERK_AUTHORIZED_PARTIES")).toBe(true);
    // A file must not imitate the platform: a deployment's own URLs come from Vercel.
    expect(isWebVariable("VERCEL_URL")).toBe(false);
    expect(isWebVariable("VERCEL_BRANCH_URL")).toBe(false);
  });

  it("trusts the origin of APP_BASE_URL when no list is given, and localhost for local development", () => {
    expect(parties(base)).toEqual(["http://localhost:3000"]);
    expect(parties({ ...base, APP_BASE_URL: "http://127.0.0.1:3100/" })).toEqual(["http://127.0.0.1:3100"]);
    expect(parties({ ...PREVIEW })).toEqual(["https://preview.example.com"]);
  });

  it("lists exactly what is named, in the form a browser sends, and never changes the result afterwards", () => {
    const listed = parties({
      ...base,
      CLERK_AUTHORIZED_PARTIES: " HTTP://Localhost:3000/ ,https://App.Example.com:443",
    });
    expect(listed).toEqual(["http://localhost:3000", "https://app.example.com"]);
    expect(Object.isFrozen(listed)).toBe(true);
  });

  it("is a list the proxy can check tokens against only when it names the pages being served", () => {
    expect(problems({ ...base, CLERK_AUTHORIZED_PARTIES: "https://other.example.com" })).toEqual([
      "CLERK_AUTHORIZED_PARTIES must list the origin of APP_BASE_URL",
    ]);
    expect(problems({ ...base, APP_BASE_URL: "https://app.example.com/x" })).toEqual([
      "APP_BASE_URL must be an http(s) origin (scheme, host, optional port; no path or credentials)",
      "CLERK_AUTHORIZED_PARTIES is required (comma-separated frontend origins)",
    ]);
  });

  it("names the variable, never the value, of an entry that is no origin", () => {
    const canary = "https://CANARY-user@a.example/CANARY";
    const found = problems({ ...base, CLERK_AUTHORIZED_PARTIES: `http://localhost:3000,${canary}` });
    expect(found).toEqual([
      "CLERK_AUTHORIZED_PARTIES must list http(s) origins (scheme, host, optional port; no path or credentials)",
    ]);
    expect(found.join()).not.toContain("CANARY");
    expect(problems({ ...base, APP_BASE_URL: canary }).join()).not.toContain("CANARY");
  });

  it("makes a deployment say where it is served from, over https in production", () => {
    expect(problems({ ...PREVIEW, APP_BASE_URL: undefined })).toEqual([
      "APP_BASE_URL is required when APP_ENV=preview",
    ]);
    expect(problems({ ...PREVIEW, APP_BASE_URL: " " })).toEqual([
      "APP_BASE_URL is required when APP_ENV=preview",
    ]);
    const production = {
      ...NAMED,
      APP_ENV: "production",
      NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: livePublishable,
      CLERK_SECRET_KEY: "sk_live_not_a_real_secret",
    };
    expect(problems({ ...production, APP_BASE_URL: "http://app.example.com" })).toEqual([
      "APP_BASE_URL must use https in production",
    ]);
    expect(problems({ ...production, APP_BASE_URL: "HTTPS://App.Example.com:443/" })).toEqual([]);
    // Local development and the tests keep the default.
    expect(problems({ ...base, APP_ENV: "test" })).toEqual([]);
  });

  describe("on a Vercel preview deployment", () => {
    it("also trusts the deployment's own origin and its branch's, after the list and without repeating it", () => {
      expect(parties({ ...PREVIEW, ...PLATFORM })).toEqual([
        "https://preview.example.com",
        "https://my-app-abc123-team.vercel.app",
        "https://my-app-git-feature-team.vercel.app",
      ]);
      expect(
        parties({
          ...PREVIEW,
          ...PLATFORM,
          CLERK_AUTHORIZED_PARTIES: "https://preview.example.com,https://my-app-abc123-team.vercel.app",
        }),
      ).toEqual([
        "https://preview.example.com",
        "https://my-app-abc123-team.vercel.app",
        "https://my-app-git-feature-team.vercel.app",
      ]);
    });

    it.each([
      ["a staging project on Vercel production", { ...PLATFORM, VERCEL_ENV: "production" }],
      ["a development environment", { ...PLATFORM, VERCEL_ENV: "development", APP_ENV: "local" }],
      ["no platform environment", { ...PLATFORM, VERCEL_ENV: undefined }],
    ])("derives nothing on %s", (_label, platform) => {
      expect(parties({ ...PREVIEW, ...platform })).toEqual(["https://preview.example.com"]);
    });

    it("derives nothing in production, even with a preview-looking URL", () => {
      const production = {
        ...NAMED,
        APP_ENV: "production",
        NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: livePublishable,
        CLERK_SECRET_KEY: "sk_live_not_a_real_secret",
        VERCEL_ENV: "production",
        VERCEL_URL: PLATFORM.VERCEL_URL,
      };
      expect(parties(production)).toEqual(["https://app.example.com"]);
    });

    it.each([
      "https://my-app-abc123-team.vercel.app",
      "my-app-abc123-team.vercel.app/x",
      "my-app-abc123-team.vercel.app:3000",
      "MY-APP.vercel.app",
      " my-app-abc123-team.vercel.app",
      "*.vercel.app",
      "localhost",
      "",
    ])("takes only a bare hostname from the platform, not %j", (value) => {
      expect(parties({ ...PREVIEW, ...PLATFORM, VERCEL_URL: value, VERCEL_BRANCH_URL: undefined })).toEqual([
        "https://preview.example.com",
      ]);
    });
  });

  describe("under APP_ENV=test (no Clerk instance)", () => {
    it("defaults nothing, so the proxy checks no token, and checks no entry", () => {
      expect(parties({ APP_ENV: "test" })).toEqual([]);
      expect(parties({ APP_ENV: "test", CLERK_AUTHORIZED_PARTIES: "not an origin" })).toEqual([
        "not an origin",
      ]);
    });
  });
});
