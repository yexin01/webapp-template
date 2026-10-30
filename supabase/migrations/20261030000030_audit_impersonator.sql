-- Who acted as the user, when someone else started the session (ADM-01).
--
-- A Clerk impersonation (dashboard, actor token, agent task) carries the
-- actor in the session token. When the application accepts such sessions
-- (CLERK_ALLOW_IMPERSONATION), every audit entry written during one names the
-- actor here, so an action taken as a user is never attributed to the user
-- alone. NULL: the user's own session. Request policy is unchanged: a request
-- may still only append entries whose actor_user_id is its own user.

ALTER TABLE app.audit_log ADD COLUMN impersonator_clerk_id text;
