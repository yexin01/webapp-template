-- Membership reconciliation rotation (DEC-009, TEN-07).
--
-- The scheduled reconcile-memberships job re-verifies a bounded batch of users
-- whose mirrored memberships are stale, least recently tried or read first:
-- mirrors that no request keeps fresh come before recently read ones. Every
-- attempt is recorded, whatever its outcome, so a user whose read keeps
-- failing moves behind the others instead of heading every run.

ALTER TABLE app.users ADD COLUMN memberships_checked_at timestamptz;
CREATE INDEX users_memberships_reconcile_idx
  ON app.users ((GREATEST(memberships_checked_at, memberships_verified_at)), id);

-- Only the worker records attempts. Its table-wide UPDATE on app.users (core
-- schema) already covers the column; requests (app_api) cannot write it.
GRANT UPDATE (memberships_checked_at) ON app.users TO app_worker;
