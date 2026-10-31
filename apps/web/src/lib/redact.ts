/** Client- and server-side redaction for error reports (OBS-02). Mirrors the API's patterns. */
import type { Breadcrumb, ErrorEvent, init } from "@sentry/nextjs";

/** A span as `beforeSendSpan` receives it (streamed spans, the SDK's default). */
type SpanJSON = Parameters<NonNullable<NonNullable<Parameters<typeof init>[0]>["beforeSendSpan"]>>[0];

/**
 * Query parameters whose values are credentials, one-time tokens, or session
 * IDs: Clerk's (tickets, invitations, handshakes, development sessions),
 * Stripe Checkout's session, redirect targets (which can embed either), and
 * common token names. Error reports redact them, along with any other Clerk
 * parameter; analytics masks them in the URLs it stores.
 */
export const SENSITIVE_QUERY_PARAMETERS = [
  "__clerk_ticket",
  "__clerk_invitation_token",
  "__clerk_status",
  "__clerk_handshake",
  "__clerk_handshake_nonce",
  "__clerk_db_jwt",
  "__clerk_created_session",
  "__clerk_testing_token",
  "__clerk_modal_state",
  "__clerk_redirect_url",
  "__clerk_satellite_url",
  "redirect_url",
  "session_id",
  "token",
  "access_token",
  "id_token",
  "code",
  "key",
  "api_key",
  "secret",
  "signature",
  "ticket",
] as const;

const escapeRegExp = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const SENSITIVE_PARAMETER_NAMES = [...SENSITIVE_QUERY_PARAMETERS.map(escapeRegExp), "__clerk[a-z_]*"].join(
  "|",
);

// Letters, digits, and combining accents of any script; the top-level domain is letters.
const EMAIL_LOCAL = "[\\p{L}\\p{N}\\p{M}._%+-]";
const EMAIL_DOMAIN = "@[\\p{L}\\p{N}\\p{M}.-]+\\.\\p{L}{2,}";
// Tried only from the start of a local-part run (linear, whole address), with any glued on: "a@b.cc+d@e.ff".
const EMAIL = new RegExp(
  `(?<!${EMAIL_LOCAL})${EMAIL_LOCAL}+${EMAIL_DOMAIN}(?:[\\p{N}\\p{M}._%+-]${EMAIL_LOCAL}*${EMAIL_DOMAIN})*`,
  "gu",
);

// Each pattern runs in linear time: no long run of characters is rescanned from every position in it.
const PATTERNS: [RegExp, string][] = [
  [/\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{6,}/g, "[REDACTED]"],
  [/\bwhsec_[A-Za-z0-9+/=]{6,}/g, "[REDACTED]"],
  // A JWT ([\w-] is [A-Za-z0-9_-]): tried once per run of token characters, from its first "eyJ".
  [
    /(?<![\w-])(?=[\w-]+\.[\w-]{5,}\.[\w-]{5,})([\w-]*?)\beyJ[\w-]{5,}\.[\w-]{5,}\.[\w-]{5,}/g,
    "$1[REDACTED]",
  ],
  [/\bbearer\s+[A-Za-z0-9._~+/=-]{8,}/gi, "[REDACTED]"],
  [EMAIL, "[REDACTED]"],
  [new RegExp(`([?&](?:${SENSITIVE_PARAMETER_NAMES})=)[^&\\s"']+`, "gi"), "$1[REDACTED]"],
];

/** The longest string the patterns scan, so the work per string is bounded. */
const MAX_TEXT_LENGTH = 4096;
// A token cut in half by the limit could escape its pattern, so it is dropped as well.
const TOKEN_AT_END = /(?<![\p{L}\p{N}\p{M}_.%+/=~@-])[\p{L}\p{N}\p{M}_.%+/=~@-]+$/u;

/** Redacts secret- and email-shaped text. Text past `maxLength` characters is dropped and marked. */
export function redactText(value: string, maxLength = MAX_TEXT_LENGTH): string {
  const text =
    value.length > maxLength ? `${value.slice(0, maxLength).replace(TOKEN_AT_END, "")}[truncated]` : value;
  return PATTERNS.reduce((acc, [pattern, replacement]) => acc.replace(pattern, replacement), text);
}

export function stripQuery(url: string): string {
  const end = url.search(/[?#]/);
  return end === -1 ? url : url.slice(0, end);
}

// A path segment shaped like a provider's ID: a short lower-case prefix, then 10 or more letters and digits
// (user_2abc..., org_..., cus_..., and cs_test_... for a Checkout Session). Any such segment goes, so an ID of
// a kind nobody listed does not get through. The API's Sentry scrubber does the same (observability.py).
const ID_SEGMENT = /(?<=\/)[a-z]{2,6}_(?:(?:test|live)_)?[A-Za-z0-9]{10,}(?![A-Za-z0-9_])/g;

/** `text` with each ID in a path replaced by `{id}`. */
export function stripPathIds(text: string): string {
  return text.replace(ID_SEGMENT, "{id}");
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

// DOM breadcrumbs name the element as `li.item > button.x[aria-label="…"][type="…"]`, with
// attribute values (often user content) copied unescaped. After the tag, id, and classes,
// Sentry's serializer writes these attributes, in this order, each once and only if not empty.
// A `dom.serializeAttribute` option would add others, which this would then drop.
const ELEMENT_ATTRIBUTES = ["aria-label", "type", "name", "title", "alt"];
const ATTRIBUTE_VALUE = new RegExp(`\\[(${ELEMENT_ATTRIBUTES.join("|")})="[^"]+"\\]`, "g");
const ELEMENT = `[^\\s[\\]"]+${ELEMENT_ATTRIBUTES.map((name) => `(?:\\[${name}\\])?`).join("")}`;
const ELEMENT_PATH = new RegExp(`^${ELEMENT}(?: > ${ELEMENT})*$`);

/** Keeps a DOM breadcrumb's element path and attribute names, but no attribute values. */
function scrubElementPath(path: string): string {
  const reduced = path.replace(ATTRIBUTE_VALUE, "[$1]");
  if (ELEMENT_PATH.test(reduced)) return reduced;
  // A value held quotes or brackets, so where it ends is unknown: keep the path up to the first one.
  const attributes = reduced.indexOf("[");
  return attributes === -1 ? reduced : reduced.slice(0, attributes);
}

/**
 * `beforeBreadcrumb` for the browser and server SDKs: drops input breadcrumbs,
 * keeps no attribute values in other DOM (`ui.*`) breadcrumbs, strips query
 * strings, fragments, and path IDs from URLs, and redacts the message and every
 * other data value, including the raw arguments of console breadcrumbs.
 */
export function scrubBreadcrumb(breadcrumb: Breadcrumb): Breadcrumb | null {
  if (breadcrumb.category === "ui.input") return null;
  const scrubbed: Breadcrumb = { ...breadcrumb };
  if (typeof scrubbed.message === "string") {
    scrubbed.message = redactText(scrubbed.message);
    if (scrubbed.category?.startsWith("ui.")) scrubbed.message = scrubElementPath(scrubbed.message);
  }
  if (scrubbed.data !== undefined) {
    const data = redactRecord(scrubbed.data);
    for (const key of URL_FIELDS) {
      const url = data[key];
      if (typeof url === "string") data[key] = stripPathIds(stripQuery(url));
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
    if (event.request.url) event.request.url = stripPathIds(stripQuery(event.request.url));
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

/** Span attributes holding only a query string or fragment, and the user (scrubEvent drops it too). */
const DROPPED_SPAN_ATTRIBUTE = /^(?:url\.query|url\.fragment|http\.query|http\.fragment|user\..*)$/;
// The query and fragment of each path or URL that starts a word: "/p?x", "GET /p?x", "https://h/p#x".
const URL_QUERY = /(?<!\S)((?:\/|[a-z][a-z0-9+.-]*:\/\/)[^\s?#]*)[?#]\S*/gi;

function scrubSpanText(value: string): string {
  return stripPathIds(redactText(value).replace(URL_QUERY, "$1"));
}

function scrubAttributeValue(value: unknown): unknown {
  if (typeof value === "string") return scrubSpanText(value);
  if (Array.isArray(value))
    return value.map((item: unknown) => (typeof item === "string" ? scrubSpanText(item) : item));
  if (isPlainObject(value) && "value" in value) return { ...value, value: scrubAttributeValue(value.value) };
  return value;
}

function scrubAttributes(attributes: Record<string, unknown> | undefined): void {
  if (!attributes) return;
  for (const [key, value] of Object.entries(attributes)) {
    if (DROPPED_SPAN_ATTRIBUTE.test(key)) delete attributes[key];
    else attributes[key] = scrubAttributeValue(value);
  }
}

/**
 * `beforeSendSpan` for the browser and server SDKs. Next.js records the raw
 * request URL on its spans (`http.target`, `http.url`), which `dataCollection`
 * does not filter, and `beforeSend` never sees spans. Every path or URL in the
 * span's name and attributes keeps only its origin and path, query, fragment,
 * and user attributes are dropped, and all other text is redacted.
 */
export function scrubSpan(span: SpanJSON): SpanJSON {
  try {
    span.name = scrubSpanText(span.name);
    scrubAttributes(span.attributes);
    for (const link of span.links ?? []) scrubAttributes(link.attributes);
    return span;
  } catch {
    // The SDK sends a span unmodified when this hook throws, so fail closed instead.
    return { ...span, name: UNSERIALIZABLE, attributes: {}, links: [] };
  }
}

/**
 * `createDsc` client hook for the browser and server SDKs. The trace context
 * (dynamic sampling context) names the root span as `transaction`, which
 * Next.js and the SDK can build from the raw request URL (`GET /_error`
 * becomes `GET /path?query`). The SDK sends it in envelope headers and in the
 * `baggage` header of outgoing requests, which no before* option sees.
 */
export function scrubTraceContext(context: { transaction?: string }): void {
  try {
    if (typeof context.transaction === "string") context.transaction = scrubSpanText(context.transaction);
  } catch {
    // The SDK calls client hooks without a try/catch; scrubEnvelopeHeaders still covers what is sent.
  }
}

/**
 * `beforeEnvelope` client hook for the browser and server SDKs: scrubs the
 * trace context in every envelope's headers, including one received from
 * upstream in a request's `baggage` header, which `createDsc` never sees.
 * An upstream caller sets every field of that context, so each text field is
 * scrubbed, not only the name. The SDK still forwards such a context
 * unchanged in the `baggage` header of outgoing requests.
 */
export function scrubEnvelopeHeaders(envelope: readonly [unknown, ...unknown[]]): void {
  const [headers] = envelope;
  if (!isPlainObject(headers) || !isPlainObject(headers.trace)) return;
  headers.trace = Object.fromEntries(
    Object.entries(headers.trace).map(([key, value]) => [
      key,
      typeof value === "string" ? scrubSpanText(value) : value,
    ]),
  );
}
