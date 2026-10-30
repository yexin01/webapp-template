/**
 * The pages a signed-out visitor may open (FE-05): the one allowlist. The
 * proxy (src/proxy.ts) sends every other page to sign-in, so a new page
 * requires sign-in until it is listed here on purpose, and a path this list
 * does not match fails closed.
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
