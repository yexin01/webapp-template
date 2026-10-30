/**
 * The pages a signed-out visitor may open (FE-05): the one allowlist. Two
 * gates read it, so a new page requires sign-in until it is listed here on
 * purpose, and a path this list does not match fails closed:
 *
 * - the proxy (src/proxy.ts) redirects on the server, and
 * - the page gate (src/ui/page-gate.tsx, mounted by the root layout) acts in
 *   the browser, so protection does not hang on the proxy running everywhere
 *   the app is deployed.
 *
 * "/x/*" is /x and everything below it (Clerk's sign-in and sign-up flows run
 * on sub-paths); any other entry is exactly that path. Write only paths as
 * Next.js serves them, without route groups. page-access.test.ts walks
 * src/app and fails when a page is public that was not meant to be.
 */
export const PUBLIC_PAGES = ["/", "/sign-in/*", "/sign-up/*"] as const;

export function isPublicPage(pathname: string): boolean {
  return PUBLIC_PAGES.some((entry) => {
    if (!entry.endsWith("/*")) return pathname === entry;
    const section = entry.slice(0, -2);
    return pathname === section || pathname.startsWith(`${section}/`);
  });
}

/** The longest return path sent to sign-in; a longer query string is dropped, not truncated. */
const MAX_RETURN_PATH = 1024;

/** A path on this origin: one slash first, then no second slash or backslash (`//host` names another origin) and no control character. */
function isRelativePath(value: string): boolean {
  if (!value.startsWith("/") || value.startsWith("//")) return false;
  return [...value].every((char) => {
    const code = char.codePointAt(0) ?? 0;
    return char !== "\\" && code > 0x1f && code !== 0x7f;
  });
}

/**
 * The sign-in page with the page the visitor asked for as its return path
 * (SEC-14). The return path is relative by construction: whatever the address
 * bar held, anything that is not a path on this origin becomes the home page.
 */
export function signInUrlFor(pathname: string, search = ""): string {
  const returnTo = [`${pathname}${search}`, pathname, "/"].find(
    (candidate) => candidate.length <= MAX_RETURN_PATH && isRelativePath(candidate),
  );
  return `/sign-in?redirect_url=${encodeURIComponent(returnTo ?? "/")}`;
}
