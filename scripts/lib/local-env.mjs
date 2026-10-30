/**
 * What `pnpm dev` and `pnpm check:setup` do before they start anything: obtain the
 * Stripe webhook signing secret from the Stripe CLI when it applies, then run the
 * configuration preflight with that secret in the API's environment.
 */
import { effectiveEnv } from "./env.mjs";
import { MESSAGES, listenPlan, readSigningSecret } from "./dev-stripe.mjs";
import { describeFailure, runPreflight } from "./preflight.mjs";

/**
 * @returns {Promise<{ok: boolean, apiEnv: Record<string, string>, listen: null | {apiKey: string, secret: string}}>}
 * `apiEnv` holds what only the API process gets (the signing secret). Messages go to `log`.
 */
export async function prepareLocalEnv({
  root,
  command,
  requireClerk = false,
  log = console.error,
  base = process.env,
  readSecret = readSigningSecret,
  preflight = runPreflight,
}) {
  const env = effectiveEnv(root, base);
  const apiEnv = {};
  let listen = null;

  const plan = listenPlan(env);
  if (plan.notice) log(`${command}: ${plan.notice}`);
  if (plan.run) {
    const result = await readSecret({ apiKey: plan.apiKey, base });
    if (result.status === "ok") {
      apiEnv.STRIPE_WEBHOOK_SECRET = result.secret;
      listen = { apiKey: plan.apiKey, secret: result.secret };
    } else {
      const message = result.status === "missing" ? MESSAGES.missing : result.status === "invalid" ? MESSAGES.invalid : MESSAGES.failed;
      log(`${command}: ${message}`);
    }
  }

  const checked = await preflight({
    root,
    env: { ...env, ...apiEnv },
    childEnv: { ...base, ...apiEnv },
    requireClerk,
  });
  for (const warning of checked.warnings) log(`${command}: ${warning}`);
  if (!checked.ok) log(describeFailure(command, checked.problems));
  return { ok: checked.ok, apiEnv, listen };
}
