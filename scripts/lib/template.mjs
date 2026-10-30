/**
 * Values that identify an un-initialized copy of the template. Shared by
 * init-template, its test, and check-placeholders so they cannot drift.
 */
export const ORIGINAL_NAME = "SaaS Template";
export const ORIGINAL_SLUG = "saas-template";
export const ORIGINAL_DB_PORT = "54329";

// The name lands verbatim in JSON, Python, TOML, TSX, and Markdown, and the
// API's OpenAPI generator escapes non-ASCII characters, so only printable
// ASCII that is inert in all of them is accepted. Spaces are single and
// between words, as init-template trims --name.
const SAFE_NAME = /^[A-Za-z0-9](?:[A-Za-z0-9.,()+!-]| (?! |$))*$/;
const SLUG = /^[a-z][a-z0-9-]{1,40}$/;
// A name or slug that contains the original would be renamed again on every re-run.
const ORIGINAL = /saas[ _-]?template/i;

/** A product name init-template accepts as --name. */
export function isValidName(value) {
  return typeof value === "string" && value.length <= 60 && SAFE_NAME.test(value) && !ORIGINAL.test(value);
}

/** A slug init-template accepts as --slug. */
export function isValidSlug(value) {
  return typeof value === "string" && SLUG.test(value) && !ORIGINAL.test(value);
}

/**
 * Ports the template's own servers use, so the local PostgreSQL port must not:
 * web, API, the performance server, and the e2e web and API servers
 * (apps/web/playwright.config.ts).
 */
export const TAKEN_PORTS = new Set(["3000", "8000", "8001", "3100", "8100"]);

/**
 * Community contacts are adopter-owned (DEC-012, ADR-0002): the template
 * ships placeholders that init-template replaces.
 */
export const PLACEHOLDERS = [
  { file: "SECURITY.md", token: "{{SECURITY_CONTACT}}", option: "security-contact" },
  { file: "CODE_OF_CONDUCT.md", token: "{{CONDUCT_CONTACT}}", option: "conduct-contact" },
];

const EMAIL = /^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$/;
const HTTPS_URL = /^https:\/\/[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+(:\d{1,5})?(\/[A-Za-z0-9._~%/#?=&+-]*)?$/;

/** A contact is an email address or an https URL, nothing that could inject Markdown or HTML. */
export function isValidContact(value) {
  return typeof value === "string" && value.length <= 200 && (EMAIL.test(value) || HTTPS_URL.test(value));
}

/**
 * The template maintainers' own records. An adopter's copy drops them by default
 * (`--keep-template-material` keeps them): the DEC-001 deployment proof and the
 * integration notes, and the files in the evidence folder, which describe runs
 * of the template itself. The adopter's evidence starts empty.
 */
export const TEMPLATE_ONLY_PATHS = ["proofs", "docs-notes"];
export const EVIDENCE_DIR = "dev-docs/evidence";

/** The specification stays as inherited reference; this marks the banner init-template adds to it. */
export const SPEC_FILE = "dev-docs/engineering-implementation-plan.md";
export const SPEC_BANNER_MARKER = "<!-- inherited-reference -->";
export const SPEC_BANNER = `> **Inherited reference.** This is the specification the template's code was built against, copied into this project when it was created. Requirement and decision IDs in the code and tests (for example \`ENV-02\`) refer to it. Its decision register (§3), phase plan (§18), status (§23), and links to evidence describe the template's own history: they do not block work in this project, and the evidence files were not copied. Record this project's decisions as ADRs in \`dev-docs/adr/\`.
${SPEC_BANNER_MARKER}`;

/** Prose that only makes sense in the template itself; an adopter's copy drops it, markers included. */
export const TEMPLATE_ONLY_BLOCK = /<!-- template-only:start -->[\s\S]*?<!-- template-only:end -->\n?/g;

/** Where init-template reads the adopter's versions of files that are the template's own. */
export const ADOPTER_TEMPLATES = "scripts/templates/adopter";
