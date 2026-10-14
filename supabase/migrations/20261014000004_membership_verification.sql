-- When a user's organization memberships were last read from the identity
-- provider (DEC-009, TEN-07).
--
-- A refresh is applied only if its read is at least as recent as the last one
-- applied, so a slow read can never bring back a membership that a newer read
-- removed. A miss on an organization shortly after a full read is answered
-- from the mirror instead of calling the provider again.

ALTER TABLE app.users ADD COLUMN memberships_verified_at timestamptz;
