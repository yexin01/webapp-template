/**
 * Request deadlines (ARCH-07). Dependency-free, so next.config.ts can size
 * the /api/v1 rewrite proxy from the same numbers the browser client uses.
 */

/** Every request has a deadline, so a stalled API cannot leave a screen busy forever. */
export const REQUEST_TIMEOUT_MS = 30_000;

/**
 * Calls the API answers only after reaching an external provider (Stripe,
 * the LLM, integration checks), each bounded by its own server-side timeout.
 */
export const PROVIDER_TIMEOUT_MS = 90_000;

/**
 * The `next dev` / `next start` rewrite proxy's timeout for /api/v1 (its
 * default is 30 s, after which it answers a bare 500). It outlasts the
 * longest client deadline, so the client's own deadline always decides.
 */
export const PROXY_TIMEOUT_MS = PROVIDER_TIMEOUT_MS + 5_000;
