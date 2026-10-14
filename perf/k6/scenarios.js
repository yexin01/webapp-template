// Performance scenarios PERF-01 to PERF-06 (spec §15.2). Run against the LOCAL
// perf server only (perf/server.py) unless an owner-approved run plan says
// otherwise. See perf/README.md for commands.
//
//   SCENARIO=baseline|load|stress|spike|soak|webhooks
//
// Thresholds below are ABORT CRITERIA and summary selectors, not performance
// targets. No targets exist until DEC-003 is resolved.
import { check } from "k6";
import crypto from "k6/crypto";
import exec from "k6/execution";
import http from "k6/http";
import { Counter } from "k6/metrics";

const BASE_URL = __ENV.BASE_URL || "http://127.0.0.1:8001";
const SCENARIO = __ENV.SCENARIO || "baseline";
const USERS = Number(__ENV.USERS || 200);
const ORGS = Number(__ENV.ORGS || 20);
// Must match perf/server.py. Test-only; not a credential.
const WEBHOOK_SECRET = "whsec_perf_test_only_not_a_real_secret"; // gitleaks:allow

const CLASSES = ["health", "auth_read", "tenant_list", "org_list", "billing_status", "entitlement_gated", "auth_write"];

const rate = (name, fallback) => Number(__ENV[name] || fallback);

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
    iterations: rate("WEBHOOK_EVENTS", 100) * rate("DUPLICATES", 3),
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

function user() {
  return Math.floor(Math.random() * USERS);
}

function headersFor(index) {
  const id = `perf_user_${String(index).padStart(4, "0")}`;
  return { Authorization: `Bearer perf-token-${id}`, "Content-Type": "application/json" };
}

function expect(response, status, name) {
  const ok = check(response, { [`${name} ${status}`]: (r) => r.status === status });
  if (!ok) unexpected.add(1, { class: name });
}

// One iteration exercises each endpoint class once with a random synthetic user.
export default function () {
  const index = user();
  const headers = headersFor(index);
  const org = `org_perf${String(index % ORGS).padStart(3, "0")}`;
  const pro = index % 2 === 0;
  const tag = (name) => ({ headers, tags: { class: name } });

  expect(http.get(`${BASE_URL}/api/v1/health`, { tags: { class: "health" } }), 200, "health");
  expect(http.get(`${BASE_URL}/api/v1/me`, tag("auth_read")), 200, "auth_read");
  expect(http.get(`${BASE_URL}/api/v1/projects?limit=20`, tag("tenant_list")), 200, "tenant_list");
  expect(http.get(`${BASE_URL}/api/v1/orgs/${org}/projects?limit=20`, tag("org_list")), 200, "org_list");
  expect(http.get(`${BASE_URL}/api/v1/billing/status`, tag("billing_status")), 200, "billing_status");
  // Free users are expected to get 403 here; only this request treats 403 as success.
  const gated = { ...tag("entitlement_gated"), responseCallback: http.expectedStatuses(pro ? 200 : 403) };
  expect(http.get(`${BASE_URL}/api/v1/pro/insight`, gated), pro ? 200 : 403, "entitlement_gated");

  const created = http.post(`${BASE_URL}/api/v1/projects`, JSON.stringify({ name: "perf write" }), tag("auth_write"));
  expect(created, 201, "auth_write");
  if (created.status === 201) {
    const id = created.json("id");
    expect(http.del(`${BASE_URL}/api/v1/projects/${id}`, null, tag("auth_write")), 204, "auth_write");
  }
}

function sign(payload) {
  const timestamp = Math.floor(Date.now() / 1000);
  const digest = crypto.hmac("sha256", WEBHOOK_SECRET, `${timestamp}.${payload}`, "hex");
  return `t=${timestamp},v1=${digest}`;
}

// Each unique event is delivered DUPLICATES times by concurrent VUs.
export function webhooks() {
  const events = rate("WEBHOOK_EVENTS", 100);
  // Global iteration number, so each event ID is delivered exactly DUPLICATES times.
  const n = exec.scenario.iterationInTest % events;
  const customer = `cus_perf${String((n * 2) % USERS).padStart(4, "0")}`;
  const payload = JSON.stringify({
    id: `evt_perf_${String(n).padStart(5, "0")}`,
    object: "event",
    type: "customer.subscription.updated",
    created: Math.floor(Date.now() / 1000),
    data: { object: { id: `sub_${customer.slice(4)}`, object: "subscription", customer } },
  });
  const response = http.post(`${BASE_URL}/api/v1/webhooks/stripe`, payload, {
    headers: { "Content-Type": "application/json", "Stripe-Signature": sign(payload) },
    tags: { class: "webhook" },
  });
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
    base_url: BASE_URL,
    started: new Date(Date.now() - data.state.testRunDurationMs).toISOString(),
    duration_ms: Math.round(data.state.testRunDurationMs),
    metrics,
  };
  out[`/results/${SCENARIO}-${Date.now()}.json`] = JSON.stringify(summary, null, 2);
  out.stdout = `${JSON.stringify(summary, null, 2)}\n`;
  return out;
}
