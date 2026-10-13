/** Client-side redaction for error reports (OBS-02). Mirrors the API's patterns. */

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
