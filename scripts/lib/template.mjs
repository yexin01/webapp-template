/**
 * Values that identify an un-initialized copy of the template. Shared by
 * init-template, its test, and check-placeholders so they cannot drift.
 */
export const ORIGINAL_NAME = "SaaS Template";
export const ORIGINAL_SLUG = "saas-template";
export const ORIGINAL_DB_PORT = "54329";

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
