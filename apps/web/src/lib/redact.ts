/** Client- and server-side redaction for error reports (OBS-02). Mirrors the API's patterns. */
import type { Breadcrumb, ErrorEvent } from "@sentry/nextjs";

const PATTERNS: [RegExp, string][] = [
  [/\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{6,}/g, "[REDACTED]"],
  [/\bwhsec_[A-Za-z0-9+/=]{6,}/g, "[REDACTED]"],
  [/\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}/g, "[REDACTED]"],
  [/\bbearer\s+[A-Za-z0-9._~+/=-]{8,}/gi, "[REDACTED]"],
  [/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g, "[REDACTED]"],
  [/([?&](?:token|session_id|code|key|secret|__clerk[a-z_]*)=)[^&\s"']+/gi, "$1[REDACTED]"],
];

export function redactText(value: string): string {
  return PATTERNS.reduce((acc, [pattern, replacement]) => acc.replace(pattern, replacement), value);
}

export function stripQuery(url: string): string {
  return url.split(/[?#]/)[0] ?? "";
}

/** Limits per call, like Sentry's normalizeDepth and normalizeMaxBreadth, plus a total. */
const MAX_DEPTH = 5;
const MAX_BREADTH = 1000;
const MAX_NODES = 10_000;
const UNSERIALIZABLE = "[Unserializable]";

type Walk = { ancestors: object[]; seen: WeakSet<object>; budget: number };

function isPlainObject(value: unknown): value is Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const proto: unknown = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

/** Reads and redacts one child; a getter that throws becomes a marker, not an exception. */
function redactChild(read: () => unknown, depth: number, walk: Walk): unknown {
  try {
    return redactNode(read(), depth, walk);
  } catch {
    return UNSERIALIZABLE;
  }
}

function redactNode(value: unknown, depth: number, walk: Walk): unknown {
  try {
    if (typeof value === "string") return redactText(value);
    if (value === null || typeof value !== "object") return value;
    if (walk.ancestors.includes(value)) return "[Circular]";
    if (walk.seen.has(value)) return "[Repeated]";
    if (depth >= MAX_DEPTH || walk.budget <= 0) return "[Truncated]";
    walk.budget -= 1;
    walk.seen.add(value);
    if (value instanceof Error) return redactText(`${value.name}: ${value.message}`);
    if (ArrayBuffer.isView(value) || value instanceof ArrayBuffer) return "[Binary]";
    if (!Array.isArray(value) && !isPlainObject(value)) return redactText(String(value));

    walk.ancestors.push(value);
    try {
      if (Array.isArray(value)) {
        const items: unknown[] = value;
        const out = items.slice(0, MAX_BREADTH).map((_, i) => redactChild(() => items[i], depth + 1, walk));
        if (items.length > MAX_BREADTH) out.push(`[${items.length - MAX_BREADTH} more items]`);
        return out;
      }
      const keys = Object.keys(value);
      const out: Record<string, unknown> = {};
      for (const key of keys.slice(0, MAX_BREADTH)) {
        out[redactText(key)] = redactChild(() => value[key], depth + 1, walk);
      }
      if (keys.length > MAX_BREADTH) out["[more]"] = `${keys.length - MAX_BREADTH} more keys`;
      return out;
    } finally {
      walk.ancestors.pop();
    }
  } catch {
    return UNSERIALIZABLE;
  }
}

/**
 * Redacts every string, and every object key, in a JSON-like value. Errors
 * and other class instances become redacted strings. It never throws: a value
 * that cannot be read (a throwing getter or toString, an instance without
 * Object.prototype) becomes "[Unserializable]". Cycles and repeated
 * references are cut, and depth, breadth, and the number of objects visited
 * are capped, so the cost stays bounded whatever a console call holds.
 */
export function redactDeep(value: unknown): unknown {
  return redactNode(value, 0, { ancestors: [], seen: new WeakSet(), budget: MAX_NODES });
}

/** A redacted copy of a field Sentry expects to be an object. */
function redactRecord(value: unknown): Record<string, unknown> {
  const redacted = redactDeep(value);
  return isPlainObject(redacted) ? redacted : { value: redacted };
}

/** Breadcrumb data fields that hold URLs: fetch/XHR `url`, navigation `from` and `to`. */
const URL_FIELDS = ["url", "from", "to"];

/**
 * `beforeBreadcrumb` for the browser and server SDKs: drops input breadcrumbs,
 * strips query strings and fragments from URLs, and redacts the message and
 * every other data value, including the raw arguments of console breadcrumbs.
 */
export function scrubBreadcrumb(breadcrumb: Breadcrumb): Breadcrumb | null {
  if (breadcrumb.category === "ui.input") return null;
  const scrubbed: Breadcrumb = { ...breadcrumb };
  if (typeof scrubbed.message === "string") scrubbed.message = redactText(scrubbed.message);
  if (scrubbed.data !== undefined) {
    const data = redactRecord(scrubbed.data);
    for (const key of URL_FIELDS) {
      const url = data[key];
      if (typeof url === "string") data[key] = stripQuery(url);
    }
    scrubbed.data = data;
  }
  return scrubbed;
}

/**
 * `beforeSend` for the browser and server SDKs, a second line of defense
 * behind `dataCollection`: removes the user and the request's headers,
 * cookies, body, and query string, strips the request URL, and redacts
 * exception values, the message, extra data, and attached breadcrumbs.
 */
export function scrubEvent(event: ErrorEvent): ErrorEvent {
  delete event.user;
  if (event.request) {
    delete event.request.headers;
    delete event.request.cookies;
    delete event.request.data;
    delete event.request.query_string;
    if (event.request.url) event.request.url = stripQuery(event.request.url);
  }
  for (const exception of event.exception?.values ?? []) {
    if (exception.value) exception.value = redactText(exception.value);
  }
  if (event.message) event.message = redactText(event.message);
  if (event.logentry) {
    if (event.logentry.message) event.logentry.message = redactText(event.logentry.message);
    if (event.logentry.params) event.logentry.params = [redactDeep(event.logentry.params)].flat();
  }
  if (event.extra) event.extra = redactRecord(event.extra);
  if (event.breadcrumbs) {
    event.breadcrumbs = event.breadcrumbs.flatMap((breadcrumb) => scrubBreadcrumb(breadcrumb) ?? []);
  }
  return event;
}
