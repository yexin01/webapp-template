-- The open Checkout Session of a user (DEC-013, option B).
--
-- A user may have at most one session they can still complete. Starting
-- another checkout expires this one at Stripe first, so two browser tabs can
-- never both buy a subscription. The row holds Stripe's session ID (not the
-- URL, which would let anyone who reads it open the user's checkout), the key
-- the session was created with (so a retry of the same request is recognized),
-- and when Stripe expires it. It is replaced or deleted by the next checkout.
--
-- Requests read and write only their own row. The worker role never touches
-- the table, so it has no grant and no policy.

CREATE TABLE app.billing_checkout_sessions (
  user_id           uuid PRIMARY KEY REFERENCES app.users (id) ON DELETE CASCADE,
  stripe_session_id text NOT NULL CHECK (stripe_session_id LIKE 'cs\_%'),
  idempotency_key   text NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 200),
  expires_at        timestamptz,
  created_at        timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE app.billing_checkout_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.billing_checkout_sessions FORCE ROW LEVEL SECURITY;

GRANT SELECT, INSERT, UPDATE, DELETE ON app.billing_checkout_sessions TO app_api;
CREATE POLICY billing_checkout_sessions_api ON app.billing_checkout_sessions FOR ALL TO app_api
  USING (user_id = app.ctx_user_id())
  WITH CHECK (user_id = app.ctx_user_id());
