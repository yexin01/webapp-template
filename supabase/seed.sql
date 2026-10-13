-- Deterministic synthetic seed data for LOCAL development only (DB-09).
-- Fixed identifiers, no real personal data, idempotent.
-- These users do not exist in Clerk; they are fixtures for exploring the
-- schema and for performance tests, not accounts you can sign in with.

INSERT INTO app.users (id, clerk_user_id) VALUES
  ('00000000-0000-4000-8000-00000000a11c', 'user_seed_alice'),
  ('00000000-0000-4000-8000-0000000000b0', 'user_seed_bob')
ON CONFLICT (clerk_user_id) DO NOTHING;

INSERT INTO app.organizations (id, clerk_org_id) VALUES
  ('00000000-0000-4000-8000-0000000a0001', 'org_seedAcme'),
  ('00000000-0000-4000-8000-0000000a0002', 'org_seedGlobex')
ON CONFLICT (clerk_org_id) DO NOTHING;

INSERT INTO app.organization_memberships (org_id, user_id, role) VALUES
  ('00000000-0000-4000-8000-0000000a0001', '00000000-0000-4000-8000-00000000a11c', 'admin'),
  ('00000000-0000-4000-8000-0000000a0001', '00000000-0000-4000-8000-0000000000b0', 'member'),
  ('00000000-0000-4000-8000-0000000a0002', '00000000-0000-4000-8000-0000000000b0', 'admin')
ON CONFLICT (org_id, user_id) DO NOTHING;

INSERT INTO app.projects (id, org_id, owner_user_id, created_by, name, created_at) VALUES
  ('00000000-0000-4000-8000-000000000101', NULL, '00000000-0000-4000-8000-00000000a11c', '00000000-0000-4000-8000-00000000a11c', 'Alice personal project', '2026-01-01T00:00:00Z'),
  ('00000000-0000-4000-8000-000000000102', NULL, '00000000-0000-4000-8000-0000000000b0', '00000000-0000-4000-8000-0000000000b0', 'Bob personal project', '2026-01-01T00:00:00Z'),
  ('00000000-0000-4000-8000-000000000201', '00000000-0000-4000-8000-0000000a0001', NULL, '00000000-0000-4000-8000-00000000a11c', 'Acme roadmap', '2026-01-02T00:00:00Z'),
  ('00000000-0000-4000-8000-000000000301', '00000000-0000-4000-8000-0000000a0002', NULL, '00000000-0000-4000-8000-0000000000b0', 'Globex launch plan', '2026-01-03T00:00:00Z')
ON CONFLICT (id) DO NOTHING;
