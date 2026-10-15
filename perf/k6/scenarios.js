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
//   VERCEL_AUTOMATION_BYPASS_SECRET  the project's Protection Bypass for Automation
//                        secret, sent as x-vercel-protection-bypass on every request
// Without them, only a local target works: perf/server.py's synthetic users
// and test-only webhook secret are the defaults.
//
// Thresholds below are ABORT CRITERIA, run-validity checks, and summary
// selectors, not performance targets. No targets exist until DEC-003 is resolved.
import { check } from "k6";
import crypto from "k6/crypto";
import { SharedArray } from "k6/data";
import exec from "k6/execution";
import http from "k6/http";
import { Counter } from "k6/metrics";

import { customerFor, syntheticUser } from "./dataset.mjs";
import { stageResults } from "./summary.mjs";

const BASE_URL = __ENV.BASE_URL || "http://127.0.0.1:8001";
const SCENARIO = __ENV.SCENARIO || "baseline";
const INCLUDE_LLM = __ENV.INCLUDE_LLM === "1";
const LABEL = (__ENV.LABEL || "").replace(/[^A-Za-z0-9_-]/g, "");

// Credentials in the URL would be written to the results file and stdout.
if (/^[a-z][a-z\d+.-]*:\/\/[^/?#]*@/i.test(BASE_URL)) {
  throw new Error("BASE_URL must not contain a user name or password.");
}

function integer(name, fallback, min = 1) {
  const raw = __ENV[name];
  const value = raw === undefined || raw === "" ? fallback : Number(raw);
  if (!Number.isInteger(value) || value < min) throw new Error(`${name} must be an integer of at least ${min}`);
  return value;
}

// Whole seconds in a duration such as 90s, 5m, or 1h30m; NaN if it is not one.
function parseDuration(text) {
  const match = /^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$/.exec(text);
  return match && text !== "" ? Number(match[1] || 0) * 3600 + Number(match[2] || 0) * 60 + Number(match[3] || 0) : NaN;
}

function seconds(name, fallback) {
  const total = parseDuration(__ENV[name] || fallback);
  if (!(total >= 1)) throw new Error(`${name} must be a duration of at least 1s, such as 90s, 5m, or 1h30m`);
  return total;
}

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

// Vercel Deployment Protection answers 401 before the app sees the request.
// The bypass header goes on every request; the value is never logged or saved.
const BYPASS = __ENV.VERCEL_AUTOMATION_BYPASS_SECRET || "";
const BYPASS_HEADERS = BYPASS ? { "x-vercel-protection-bypass": BYPASS } : {};

// perf/server.py's synthetic users. Only that local server accepts their tokens.
function localUsers() {
  const orgs = integer("ORGS", 20);
  return Array.from({ length: integer("USERS", 200) }, (_, i) => syntheticUser(i, orgs));
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

// Arrival rates are whole iterations per second (k6 rejects fractions).
const perSecond = (value) => Math.max(1, Math.round(value));
const maxVUs = (fallback) => integer("MAX_VUS", fallback);
// A level reached at once and then held: a zero-length stage jumps, the next one holds.
const step = (target, duration) => [
  { target, duration: "0s" },
  { target, duration },
];

function arrivalScenario(name) {
  switch (name) {
    // PERF-01: low, steady load across every endpoint class.
    case "baseline": {
      const vus = maxVUs(20);
      return {
        executor: "constant-arrival-rate",
        rate: integer("RATE", 5),
        timeUnit: "1s",
        duration: `${seconds("DURATION", "60s")}s`,
        preAllocatedVUs: Math.min(10, vus),
        maxVUs: vus,
      };
    }
    // PERF-02: stepped load up to the expected level, then down.
    case "load": {
      const rate = integer("RATE", 20);
      const vus = maxVUs(100);
      return {
        executor: "ramping-arrival-rate",
        startRate: perSecond(rate / 4),
        timeUnit: "1s",
        preAllocatedVUs: Math.min(20, vus),
        maxVUs: vus,
        stages: [
          { target: perSecond(rate / 4), duration: "1m" },
          ...step(perSecond(rate / 2), "1m"),
          ...step(rate, "2m"),
          { target: 0, duration: "30s" },
        ],
      };
    }
    // PERF-03: steps beyond expected load until saturation or the abort criteria trip.
    case "stress": {
      const maxRate = integer("MAX_RATE", 100);
      const hold = `${seconds("STEP", "1m")}s`;
      const levels = [10, 20, 40, 60, 80, 100].map((percent) => perSecond((maxRate * percent) / 100));
      const vus = maxVUs(300);
      return {
        executor: "ramping-arrival-rate",
        startRate: levels[0],
        timeUnit: "1s",
        preAllocatedVUs: Math.min(50, vus),
        maxVUs: vus,
        stages: levels.flatMap((level) => step(level, hold)),
      };
    }
    // PERF-04: sudden burst from idle.
    case "spike": {
      const spikeRate = integer("SPIKE_RATE", 60);
      const vus = maxVUs(300);
      return {
        executor: "ramping-arrival-rate",
        startRate: 0,
        timeUnit: "1s",
        preAllocatedVUs: Math.min(50, vus),
        maxVUs: vus,
        stages: [
          { target: 0, duration: "10s" },
          { target: spikeRate, duration: "5s" },
          { target: spikeRate, duration: "30s" },
          { target: 0, duration: "10s" },
        ],
      };
    }
    // PERF-05: sustained moderate load.
    case "soak": {
      const vus = maxVUs(50);
      return {
        executor: "constant-arrival-rate",
        rate: integer("RATE", 10),
        timeUnit: "1s",
        duration: `${seconds("DURATION", "30m")}s`,
        preAllocatedVUs: Math.min(20, vus),
        maxVUs: vus,
      };
    }
    default:
      return null;
  }
}

// PERF-06: concurrent and duplicate signed webhook deliveries.
function webhookScenario() {
  const vus = integer("WEBHOOK_VUS", 20);
  if (vus < DUPLICATES) {
    throw new Error(`WEBHOOK_VUS (${vus}) must be at least DUPLICATES (${DUPLICATES}), or the copies cannot arrive together`);
  }
  if (vus > WEBHOOK_EVENTS * DUPLICATES) {
    throw new Error(`WEBHOOK_VUS (${vus}) must be at most WEBHOOK_EVENTS × DUPLICATES (${WEBHOOK_EVENTS * DUPLICATES})`);
  }
  if (USERS < 2) throw new Error("PERF-06 needs USERS of at least 2: its customers are the odd-numbered users");
  return { executor: "shared-iterations", exec: "webhooks", vus, iterations: WEBHOOK_EVENTS * DUPLICATES, maxDuration: "5m" };
}

const SCENARIO_CONFIG = SCENARIO === "webhooks" ? webhookScenario() : arrivalScenario(SCENARIO);
if (!SCENARIO_CONFIG) throw new Error(`Unknown SCENARIO ${SCENARIO}`);

// The offered load, stage by stage, in iterations per second. Zero-length
// stages are jumps between levels and offer nothing.
function profileOf(config) {
  if (config.executor === "constant-arrival-rate") {
    return [{ stage: 0, from: config.rate, to: config.rate, seconds: parseDuration(config.duration) }];
  }
  if (config.executor !== "ramping-arrival-rate") return [];
  let from = config.startRate;
  return config.stages.map((s, stage) => {
    const segment = { stage, from, to: s.target, seconds: parseDuration(s.duration) };
    from = s.target;
    return segment;
  });
}

const PROFILE = profileOf(SCENARIO_CONFIG).filter((s) => s.seconds > 0);
const OFFERED_ITERATIONS = PROFILE.reduce((sum, s) => sum + ((s.from + s.to) / 2) * s.seconds, 0);
// Baseline, load, and soak measure a load the target is meant to keep up with.
// If it drops more than 1% of the offered iterations, it did not receive that
// load, and the run fails. Stress and spike only report it.
const DROPPED_LIMIT = ["baseline", "load", "soak"].includes(SCENARIO) ? Math.floor(OFFERED_ITERATIONS / 100) : null;

function stageAt(elapsedSeconds) {
  let end = 0;
  for (const segment of PROFILE) {
    end += segment.seconds;
    if (elapsedSeconds < end) return segment.stage;
  }
  return PROFILE[PROFILE.length - 1].stage;
}

const WEBHOOK_RESULTS = ["processed", "ignored", "needs_reconciliation", "failed", "received", "busy", "error"];

const classThresholds = Object.fromEntries(
  CLASSES.map((name) => [`http_req_duration{class:${name}}`, ["max>=0"]]),
);
// Per-stage figures for the summary: iterations started, and the requests they
// sent with their error rate and latency.
const stageThresholds = Object.fromEntries(
  PROFILE.flatMap(({ stage }) => [
    [`iteration_starts{stage:${stage}}`, ["count>=0"]],
    [`http_reqs{stage:${stage}}`, ["count>=0"]],
    [`http_req_failed{stage:${stage}}`, ["rate>=0"]],
    [`http_req_duration{stage:${stage}}`, ["max>=0"]],
  ]),
);
const webhookThresholds = {
  "http_req_duration{class:webhook}": ["max>=0"],
  // Every delivery, duplicates included, must be acknowledged as processed:
  // an event that did no synchronization work proves nothing about duplicates.
  "webhook_results{result:processed}": [`count==${WEBHOOK_EVENTS * DUPLICATES}`],
  ...Object.fromEntries(
    WEBHOOK_RESULTS.filter((r) => r !== "processed").map((r) => [`webhook_results{result:${r}}`, ["count>=0"]]),
  ),
};

export const options = {
  scenarios: { [SCENARIO]: SCENARIO_CONFIG },
  summaryTrendStats: ["min", "med", "avg", "p(90)", "p(95)", "p(99)", "max"],
  thresholds: {
    // Abort criteria: an error-rate surge stops the run early. The rate is
    // cumulative over the run, so late in a long soak a short surge may not
    // reach it; read the per-stage results as well.
    http_req_failed: [{ threshold: "rate<0.05", abortOnFail: true, delayAbortEval: "10s" }],
    ...(SCENARIO === "webhooks" ? webhookThresholds : { ...classThresholds, ...stageThresholds }),
    ...(DROPPED_LIMIT === null ? {} : { dropped_iterations: [`count<=${DROPPED_LIMIT}`] }),
  },
};

const unexpected = new Counter("unexpected_responses");
const iterationStarts = new Counter("iteration_starts");
const webhookResults = new Counter("webhook_results");

// One identifier per run, shared by every VU, so event IDs never repeat across runs.
export function setup() {
  return { run: Date.now().toString(36) };
}

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

// health and webhook requests carry no bearer token: the app never answers them
// with 401, but Vercel Deployment Protection does.
function expect(response, status, name, bearer = true) {
  if (response.status === 401 && !bearer && !LOCAL_TARGET) {
    exec.test.abort(
      `${name} got 401: ${TARGET} is behind Vercel Deployment Protection. ` +
        (BYPASS
          ? "It rejected VERCEL_AUTOMATION_BYPASS_SECRET; check that it is this project's Protection Bypass for Automation secret."
          : "Set VERCEL_AUTOMATION_BYPASS_SECRET to the project's Protection Bypass for Automation secret.") +
        " See perf/run-plan-template.md.",
    );
  }
  if (response.status === 401) {
    // Every later authenticated request would fail the same way.
    exec.test.abort(
      `${name} got 401: the target rejected the bearer token (wrong issuer or authorized party, expired, ` +
        "or USERS larger than perf/server.py --users). See perf/run-plan-template.md.",
    );
  }
  const ok = check(response, { [`${name} ${status}`]: (r) => r.status === status });
  if (!ok) unexpected.add(1, { class: name });
  return ok;
}

// One iteration exercises each endpoint class once as one synthetic user.
export default function () {
  // Tags this iteration's requests with the stage it started in.
  exec.vu.metrics.tags.stage = String(stageAt((Date.now() - exec.scenario.startTime) / 1000));
  iterationStarts.add(1);
  const user = USER_LIST[userFor(exec.scenario.iterationInTest)];
  const headers = { ...BYPASS_HEADERS, Authorization: `Bearer ${user.token}`, "Content-Type": "application/json" };
  const tag = (name) => ({ headers, tags: { class: name } });

  const health = http.get(`${BASE_URL}/api/v1/health`, { headers: BYPASS_HEADERS, tags: { class: "health" } });
  expect(health, 200, "health", false);
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

function webhookResult(response) {
  if (response.status === 503) return "busy";
  if (response.status !== 200) return "error";
  let status = null;
  try {
    status = response.json("status");
  } catch (_error) {
    return "error";
  }
  return WEBHOOK_RESULTS.includes(status) ? status : "error";
}

// PERF-06. Shared iterations are numbered globally and handed out in order, so
// the WEBHOOK_VUS requests in flight at any moment are consecutive iterations:
//   - the DUPLICATES copies of an event are consecutive iterations, so they are
//     delivered at the same time and contend for the same ledger row;
//   - EVENTS_PER_CUSTOMER consecutive events share a customer, so distinct
//     events contend for that customer's synchronization lock.
// Copies are identical apart from the signature timestamp, like Stripe retries.
// Event IDs carry the run's identifier, so every run sends new events.
export function webhooks(data) {
  const n = Math.floor(exec.scenario.iterationInTest / DUPLICATES);
  const customer = customerFor(Math.floor(n / EVENTS_PER_CUSTOMER), USERS);
  const payload = JSON.stringify({
    id: `evt_perf_${data.run}_${String(n).padStart(5, "0")}`,
    object: "event",
    type: "customer.subscription.updated",
    created: Math.floor(exec.scenario.startTime / 1000),
    data: { object: { id: `sub_${customer.slice(4)}`, object: "subscription", customer } },
  });
  const response = http.post(`${BASE_URL}/api/v1/webhooks/stripe`, payload, {
    headers: { ...BYPASS_HEADERS, "Content-Type": "application/json", "Stripe-Signature": sign(payload) },
    tags: { class: "webhook" },
  });
  if (response.status === 400 && String(response.body).includes("WEBHOOK_SIGNATURE_INVALID")) {
    exec.test.abort("The target rejected the webhook signature: PERF_WEBHOOK_SECRET does not match its STRIPE_WEBHOOK_SECRET.");
  }
  const result = webhookResult(response);
  webhookResults.add(1, { result });
  if (expect(response, 200, "webhook", false) && result !== "processed") {
    // A 200 that did no synchronization work, for example an unknown customer.
    check(response, { "webhook processed": () => false });
    unexpected.add(1, { class: "webhook" });
  }
}

function count(metrics, name) {
  return metrics[name] ? metrics[name].values.count : 0;
}

export function handleSummary(data) {
  const out = {};
  const metrics = {};
  for (const [name, metric] of Object.entries(data.metrics)) {
    if (
      name.startsWith("http_req_duration{") ||
      name.startsWith("webhook_results") ||
      ["http_reqs", "http_req_failed", "iterations", "dropped_iterations", "vus_max", "unexpected_responses", "checks"].includes(name)
    ) {
      metrics[name] = metric.values;
    }
  }
  // Achieved against offered load per stage, over the seconds the run covered.
  // Requests are those of the iterations that started in the stage.
  const elapsed = data.state.testRunDurationMs / 1000;
  const { planned_duration_s, aborted, stages } = stageResults(PROFILE, data.metrics, elapsed);
  const errorThreshold = data.metrics.http_req_failed?.thresholds?.["rate<0.05"];
  const dropped = count(data.metrics, "dropped_iterations");
  const webhookCounts = Object.fromEntries(
    WEBHOOK_RESULTS.map((r) => [r, count(data.metrics, `webhook_results{result:${r}}`)]),
  );
  const summary = {
    scenario: SCENARIO,
    label: LABEL || null,
    include_llm: INCLUDE_LLM,
    base_url: BASE_URL,
    seed: SEED,
    users: USERS,
    users_source: __ENV.PERF_USERS_FILE ? "PERF_USERS_FILE" : "perf/server.py synthetic users",
    protection_bypass: Boolean(BYPASS),
    // The abort criterion: more than 5% of requests failed (which stops the run once crossed).
    error_threshold_crossed: errorThreshold ? !errorThreshold.ok : null,
    ...(SCENARIO === "webhooks"
      ? {
          webhooks: {
            run: data.setup_data ? data.setup_data.run : null,
            events: WEBHOOK_EVENTS,
            duplicates: DUPLICATES,
            events_per_customer: EVENTS_PER_CUSTOMER,
            vus: SCENARIO_CONFIG.vus,
            results: webhookCounts,
            all_processed: webhookCounts.processed === WEBHOOK_EVENTS * DUPLICATES,
          },
        }
      : {
          offered_iterations: Math.round(OFFERED_ITERATIONS),
          planned_duration_s,
          // Stopped (on errors, a 401, or Ctrl+C) before its last whole iteration was
          // due: stages are cut short or not reached.
          aborted,
          dropped_iterations: dropped,
          // The run was not cut short and dropped at most 1% of the offered
          // iterations: the target received the offered load.
          offered_load_delivered: !aborted && dropped <= Math.floor(OFFERED_ITERATIONS / 100),
          stages,
        }),
    started: new Date(Date.now() - data.state.testRunDurationMs).toISOString(),
    duration_ms: Math.round(data.state.testRunDurationMs),
    metrics,
  };
  out[`/results/${SCENARIO}${LABEL ? `-${LABEL}` : ""}-${Date.now()}.json`] = JSON.stringify(summary, null, 2);
  out.stdout = `${JSON.stringify(summary, null, 2)}\n`;
  return out;
}
