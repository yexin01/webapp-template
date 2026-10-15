-- Expired outbox leases (ADR-0003).
--
-- Every drain first looks for leased events whose lease ran out (the worker
-- crashed or was stopped by the platform) and turns them back into retries.
-- Only a handful of events are leased at any time, so a partial index keeps
-- that lookup independent of the backlog of pending events.

CREATE INDEX outbox_leased_idx ON app.outbox (locked_until) WHERE status = 'processing';
