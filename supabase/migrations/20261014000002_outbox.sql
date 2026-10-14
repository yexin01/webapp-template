-- Transactional outbox for domain events (ADR-0003).
--
-- A module writes an event in the same transaction as the state change it
-- describes, so the event exists if and only if the change committed. Handlers
-- run after commit (right after the response, or from the drain-outbox job) and
-- at least once, so they must be idempotent. Payloads hold internal
-- identifiers and small facts only: no personal data beyond IDs, no secrets.

CREATE TABLE app.outbox (
  id            uuid PRIMARY KEY,
  event_type    text NOT NULL CHECK (event_type ~ '^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$'),
  payload       jsonb NOT NULL DEFAULT '{}'::jsonb,
  occurred_at   timestamptz NOT NULL DEFAULT now(),
  available_at  timestamptz NOT NULL DEFAULT now(),
  status        text NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'processing', 'processed', 'dead')),
  attempts      integer NOT NULL DEFAULT 0,
  locked_until  timestamptz,
  last_error    text,
  processed_at  timestamptz
);

-- Due work: pending rows by availability, plus expired leases.
CREATE INDEX outbox_due_idx ON app.outbox (available_at, id) WHERE status IN ('pending', 'processing');
CREATE INDEX outbox_processed_idx ON app.outbox (processed_at) WHERE status = 'processed';

ALTER TABLE app.outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.outbox FORCE ROW LEVEL SECURITY;

-- Requests may append events while acting for a user; they can never read,
-- change, or delete outbox rows.
GRANT INSERT ON app.outbox TO app_api;
CREATE POLICY outbox_api_insert ON app.outbox FOR INSERT TO app_api
  WITH CHECK (app.ctx_user_id() IS NOT NULL AND status = 'pending' AND attempts = 0);

-- The worker role dispatches events.
GRANT SELECT, INSERT, UPDATE, DELETE ON app.outbox TO app_worker;
CREATE POLICY outbox_worker ON app.outbox FOR ALL TO app_worker USING (true) WITH CHECK (true);
