// Performance scenarios PERF-01 to PERF-07 (spec §15.2). Run against the LOCAL
// perf server only (perf/server.py) unless an owner-approved run plan says
// otherwise. See perf/README.md for commands.
//
//   SCENARIO=baseline|load|stress|spike|soak|webhooks
//   INCLUDE_LLM=1   adds the mocked LLM check to each iteration (PERF-07)
//   LABEL=name      tags the results file, e.g. a PERF-07 server configuration
//   SEED=n          request seed: which synthetic user each iteration uses (default 1)
//
// Credentials for a deployed target (never logged; see perf/run-plan-template.md):
//   PERF_USERS_FILE      JSON array of synthetic users: [{"token", "org", "pro"}, ...]
//   PERF_WEBHOOK_SECRET  the target's test-only STRIPE_WEBHOOK_SECRET (PERF-06)
// Without them, only a local target works: perf/server.py's synthetic users
// and test-only webhook secret are the defaults.
//
// Thresholds below are ABORT CRITERIA and summary selectors, not performance
// targets. No targets exist until DEC-003 is resolved.
import { check } from "k6";
import crypto from "k6/crypto";
import { SharedArray } from "k6/data";
import exec from "k6/execution";
import http from "k6/http";
import { Counter } from "k6/metrics";

const BASE_URL = __ENV.BASE_URL || "http://127.0.0.1:8001";
const SCENARIO = __ENV.SCENARIO || "baseline";
const INCLUDE_LLM = __ENV.INCLUDE_LLM === "1";
const LABEL = (__ENV.LABEL || "").replace(/[^A-Za-z0-9_-]/g, "");

function integer(name, fallback, min = 1) {
  const raw = __ENV[name];
  const value = raw === undefined || raw === "" ? fallback : Number(raw);
  if (!Number.isInteger(value) || value < min) throw new Error(`${name} must be an integer of at least ${min}`);
  return value;
}

const rate = (name, fallback) => Number(__ENV[name] || fallback);
const SEED = integer("SEED", 1, 0);
const WEBHOOK_EVENTS = integer("WEBHOOK_EVENTS", 100);
const DUPLICATES = integer("DUPLICATES", 3);
const EVENTS_PER_CUSTOMER = integer("EVENTS_PER_CUSTOMER", 4);

// Same hosts as scripts/perf.mjs. Anything else is a deployed target.
const TARGET_HOST = (
  (BASE_URL.match(/^[a-z][a-z\d+.-]*:\/\/(?:[^@/?#]*@)?(\[[^\]]*\]|[^:/?#]*)/i) || [])[1] || ""
).toLowerCase();
const LOCAL_TARGET = ["localhost", "127.0.0.1", "host.docker.internal"].includes(TARGET_HOST);
const TARGET = TARGET_HOST || "The BASE_URL host";

// perf/server.py's synthetic users: user i holds token perf-token-perf_user_NNNN,
// belongs to organization i % ORGS, and is Pro when i is even. Only that local
// server accepts these tokens.
function localUsers() {
  const orgs = integer("ORGS", 20);
  return Array.from({ length: integer("USERS", 200) }, (_, i) => ({
    token: `perf-token-perf_user_${String(i).padStart(4, "0")}`,
    org: `org_perf${String(i % orgs).padStart(3, "0")}`,
    pro: i % 2 === 0,
  }));
}

// Error messages name the entry and field only: the file holds bearer tokens.
function fileUsers(path) {
  const text = open(path);
  let users;
  try {
    users = JSON.parse(text);
  } catch (_error) {
    throw new Error("PERF_USERS_FILE is not valid JSON");
  }
  if (!Array.isArray(users) || users.length === 0) throw new Error("PERF_USERS_FILE must be a non-empty JSON array");
  users.forEach((u, i) => {
    const valid =
      u !== null &&
      typeof u === "object" &&
      typeof u.token === "string" &&
      /^\S+$/.test(u.token) &&
      typeof u.org === "string" &&
      u.org !== "" &&
      typeof u.pro === "boolean";
    if (!valid) throw new Error(`PERF_USERS_FILE entry ${i} needs a "token" string, an "org" string, and a boolean "pro"`);
  });
  const count = integer("USERS", users.length);
  if (count > users.length) throw new Error(`USERS=${count}, but PERF_USERS_FILE has ${users.length} entries`);
  return users.slice(0, count).map(({ token, org, pro }) => ({ token, org, pro }));
}

const USER_LIST = new SharedArray("perf users", () => {
  if (__ENV.PERF_USERS_FILE) return fileUsers(__ENV.PERF_USERS_FILE);
  if (!LOCAL_TARGET && SCENARIO !== "webhooks") {
    // Fail before the first request instead of aborting on a wall of 401s.
    throw new Error(
      `${TARGET} is not the local perf server, which is the only target that accepts the built-in ` +
        "synthetic tokens. Set PERF_USERS_FILE to the synthetic users' session tokens (perf/run-plan-template.md).",
    );
  }
  return localUsers();
});
const USERS = USER_LIST.length;

// Test-only signing secret shared with perf/server.py. Not a credential.
const LOCAL_WEBHOOK_SECRET = "whsec_perf_test_only_not_a_real_secret"; // gitleaks:allow
const WEBHOOK_SECRET = __ENV.PERF_WEBHOOK_SECRET || (LOCAL_TARGET ? LOCAL_WEBHOOK_SECRET : "");
if (SCENARIO === "webhooks" && !WEBHOOK_SECRET) {
  throw new Error(
    `${TARGET} is not the local perf server. Set PERF_WEBHOOK_SECRET to the target's ` +
      "test-only STRIPE_WEBHOOK_SECRET (perf/run-plan-template.md).",
  );
}

const CLASSES = [
  "health",
  "auth_read",
  "tenant_list",
  "org_list",
  "billing_status",
  "entitlement_gated",
  "auth_write",
  ...(INCLUDE_LLM ? ["llm_check"] : []),
];

const SCENARIOS = {
  // PERF-01: low, steady load across every endpoint class.
  baseline: {
    executor: "constant-arrival-rate",
    rate: rate("RATE", 5),
    timeUnit: "1s",
    duration: __ENV.DURATION || "60s",
    preAllocatedVUs: 10,
    maxVUs: 20,
  },
  // PERF-02: stepped load up to the expected level.
  load: {
    executor: "ramping-arrival-rate",
    startRate: 1,
    timeUnit: "1s",
    preAllocatedVUs: 20,
    maxVUs: 100,
    stages: [
      { target: rate("RATE", 20) / 4, duration: "1m" },
      { target: rate("RATE", 20) / 2, duration: "1m" },
      { target: rate("RATE", 20), duration: "2m" },
      { target: 0, duration: "30s" },
    ],
  },
  // PERF-03: step beyond expected load until saturation or the abort criteria trip.
  stress: {
    executor: "ramping-arrival-rate",
    startRate: 5,
    timeUnit: "1s",
    preAllocatedVUs: 50,
    maxVUs: rate("MAX_VUS", 300),
    stages: [10, 20, 40, 60, 80, 100].map((step) => ({
      target: (rate("MAX_RATE", 100) * step) / 100,
      duration: __ENV.STEP || "1m",
    })),
  },
  // PERF-04: sudden burst from idle.
  spike: {
    executor: "ramping-arrival-rate",
    startRate: 0,
    timeUnit: "1s",
    preAllocatedVUs: 50,
    maxVUs: rate("MAX_VUS", 300),
    stages: [
      { target: 0, duration: "10s" },
      { target: rate("SPIKE_RATE", 60), duration: "5s" },
      { target: rate("SPIKE_RATE", 60), duration: "30s" },
      { target: 0, duration: "10s" },
    ],
  },
  // PERF-05: sustained moderate load.
  soak: {
    executor: "constant-arrival-rate",
    rate: rate("RATE", 10),
    timeUnit: "1s",
    duration: __ENV.DURATION || "30m",
    preAllocatedVUs: 20,
    maxVUs: 50,
  },
  // PERF-06: concurrent and duplicate signed webhook deliveries.
  webhooks: {
    executor: "shared-iterations",
    exec: "webhooks",
    vus: rate("WEBHOOK_VUS", 20),
    iterations: WEBHOOK_EVENTS * DUPLICATES,
    maxDuration: "5m",
  },
};

if (!SCENARIOS[SCENARIO]) throw new Error(`Unknown SCENARIO ${SCENARIO}`);

const classThresholds = Object.fromEntries(
  CLASSES.map((name) => [`http_req_duration{class:${name}}`, ["max>=0"]]),
);

export const options = {
  scenarios: { [SCENARIO]: SCENARIOS[SCENARIO] },
  summaryTrendStats: ["min", "med", "avg", "p(90)", "p(95)", "p(99)", "max"],
  thresholds: {
    // Abort criteria: an error-rate surge stops the run early.
    http_req_failed: [{ threshold: "rate<0.05", abortOnFail: true, delayAbortEval: "10s" }],
    ...(SCENARIO === "webhooks" ? { "http_req_duration{class:webhook}": ["max>=0"] } : classThresholds),
  },
};

const unexpected = new Counter("unexpected_responses");

// The user for an iteration depends only on SEED and the global iteration
// number, so a rerun with the same SEED sends the same request sequence
// whichever VU runs each iteration (spec §15.2 fixed seeds). The mixing step
// is the murmur3 32-bit finalizer.
function userFor(iteration) {
  let x = (SEED ^ Math.imul(iteration + 1, 0x9e3779b9)) >>> 0;
  x = Math.imul(x ^ (x >>> 16), 0x85ebca6b);
  x = Math.imul(x ^ (x >>> 13), 0xc2b2ae35);
  return ((x ^ (x >>> 16)) >>> 0) % USERS;
}

function expect(response, status, name) {
  if (response.status === 401) {
    // Every later authenticated request would fail the same way.
    exec.test.abort(
      `${name} got 401: the target rejected the bearer token (wrong issuer or authorized party, expired, ` +
        "or USERS larger than perf/server.py --users). See perf/run-plan-template.md.",
    );
  }
  const ok = check(response, { [`${name} ${status}`]: (r) => r.status === status });
  if (!ok) unexpected.add(1, { class: name });
}

// One iteration exercises each endpoint class once as one synthetic user.
export default function () {
  const user = USER_LIST[userFor(exec.scenario.iterationInTest)];
  const headers = { Authorization: `Bearer ${user.token}`, "Content-Type": "application/json" };
  const tag = (name) => ({ headers, tags: { class: name } });

  expect(http.get(`${BASE_URL}/api/v1/health`, { tags: { class: "health" } }), 200, "health");
  expect(http.get(`${BASE_URL}/api/v1/me`, tag("auth_read")), 200, "auth_read");
  expect(http.get(`${BASE_URL}/api/v1/projects?limit=20`, tag("tenant_list")), 200, "tenant_list");
  const orgProjects = `${BASE_URL}/api/v1/orgs/${encodeURIComponent(user.org)}/projects?limit=20`;
  expect(http.get(orgProjects, tag("org_list")), 200, "org_list");
  expect(http.get(`${BASE_URL}/api/v1/billing/status`, tag("billing_status")), 200, "billing_status");
  // Free users are expected to get 403 here; only this request treats 403 as success.
  const gated = { ...tag("entitlement_gated"), responseCallback: http.expectedStatuses(user.pro ? 200 : 403) };
  expect(http.get(`${BASE_URL}/api/v1/pro/insight`, gated), user.pro ? 200 : 403, "entitlement_gated");

  const created = http.post(`${BASE_URL}/api/v1/projects`, JSON.stringify({ name: "perf write" }), tag("auth_write"));
  expect(created, 201, "auth_write");
  if (created.status === 201) {
    const id = created.json("id");
    expect(http.del(`${BASE_URL}/api/v1/projects/${id}`, null, tag("auth_write")), 204, "auth_write");
  }

  if (INCLUDE_LLM) {
    expect(http.post(`${BASE_URL}/api/v1/llm/check`, null, tag("llm_check")), 200, "llm_check");
  }
}

function sign(payload) {
  const timestamp = Math.floor(Date.now() / 1000);
  const digest = crypto.hmac("sha256", WEBHOOK_SECRET, `${timestamp}.${payload}`, "hex");
  return `t=${timestamp},v1=${digest}`;
}

// The billing customers perf/server.py seeds: cus_perfNNNN for even user indexes.
function customerFor(group) {
  return `cus_perf${String(2 * (group % Math.ceil(USERS / 2))).padStart(4, "0")}`;
}

// PERF-06. Shared iterations are numbered globally and handed out in order, so
// the WEBHOOK_VUS requests in flight at any moment are consecutive iterations:
//   - the DUPLICATES copies of an event are consecutive iterations, so they are
//     delivered at the same time and contend for the same ledger row;
//   - EVENTS_PER_CUSTOMER consecutive events share a customer, so distinct
//     events contend for that customer's synchronization lock.
// Copies are identical apart from the signature timestamp, like Stripe retries.
export function webhooks() {
  const n = Math.floor(exec.scenario.iterationInTest / DUPLICATES);
  const customer = customerFor(Math.floor(n / EVENTS_PER_CUSTOMER));
  const payload = JSON.stringify({
    id: `evt_perf_${String(n).padStart(5, "0")}`,
    object: "event",
    type: "customer.subscription.updated",
    created: Math.floor(exec.scenario.startTime / 1000),
    data: { object: { id: `sub_${customer.slice(4)}`, object: "subscription", customer } },
  });
  const response = http.post(`${BASE_URL}/api/v1/webhooks/stripe`, payload, {
    headers: { "Content-Type": "application/json", "Stripe-Signature": sign(payload) },
    tags: { class: "webhook" },
  });
  if (response.status === 400 && String(response.body).includes("WEBHOOK_SIGNATURE_INVALID")) {
    exec.test.abort("The target rejected the webhook signature: PERF_WEBHOOK_SECRET does not match its STRIPE_WEBHOOK_SECRET.");
  }
  expect(response, 200, "webhook");
}

export function handleSummary(data) {
  const out = {};
  const metrics = {};
  for (const [name, metric] of Object.entries(data.metrics)) {
    if (name.startsWith("http_req_duration{") || ["http_reqs", "http_req_failed", "iterations", "dropped_iterations", "vus_max", "unexpected_responses", "checks"].includes(name)) {
      metrics[name] = metric.values;
    }
  }
  const summary = {
    scenario: SCENARIO,
    label: LABEL || null,
    include_llm: INCLUDE_LLM,
    base_url: BASE_URL,
    seed: SEED,
    users: USERS,
    users_source: __ENV.PERF_USERS_FILE ? "PERF_USERS_FILE" : "perf/server.py synthetic users",
    ...(SCENARIO === "webhooks"
      ? { webhooks: { events: WEBHOOK_EVENTS, duplicates: DUPLICATES, events_per_customer: EVENTS_PER_CUSTOMER } }
      : {}),
    started: new Date(Date.now() - data.state.testRunDurationMs).toISOString(),
    duration_ms: Math.round(data.state.testRunDurationMs),
    metrics,
  };
  out[`/results/${SCENARIO}${LABEL ? `-${LABEL}` : ""}-${Date.now()}.json`] = JSON.stringify(summary, null, 2);
  out.stdout = `${JSON.stringify(summary, null, 2)}\n`;
  return out;
}
