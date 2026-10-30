/**
 * The Clerk placeholder pair that .env.example ships. It is well formed, so both
 * apps build and start without a Clerk account, and fake: the publishable key
 * names a host under the reserved .invalid domain, which nobody can register,
 * so no Clerk instance can ever match it. It means "Clerk is not configured".
 */
const PLACEHOLDER_HOST = "clerk.not-configured-yet.invalid";
export const PLACEHOLDER_PUBLISHABLE_KEY = `pk_test_${Buffer.from(`${PLACEHOLDER_HOST}$`).toString("base64")}`;
export const PLACEHOLDER_SECRET_KEY = "sk_test_clerk_not_configured_yet";

function frontendHost(publishableKey) {
  const encoded = /^pk_(?:test|live)_(.+)$/.exec(publishableKey ?? "")?.[1];
  return encoded ? Buffer.from(encoded, "base64").toString("utf8").replace(/\$$/, "") : "";
}

/**
 * Where the Clerk keys stand:
 * - "missing": neither key is set;
 * - "legacy-placeholder": the older `replace_me` pair, which both apps reject as malformed;
 * - "placeholder": the shipped pair, or either key of it: Clerk is not configured, sign-in is unavailable;
 * - "configured": anything else (the apps' own validators judge it).
 */
export function clerkKeyState(env) {
  const publishable = env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY?.trim() ?? "";
  const secret = env.CLERK_SECRET_KEY?.trim() ?? "";
  if (!publishable && !secret) return "missing";
  if (/replace_me/i.test(publishable) || /replace_me/i.test(secret)) return "legacy-placeholder";
  if (frontendHost(publishable).endsWith(".invalid") || /not_configured/i.test(secret)) return "placeholder";
  return "configured";
}
