-- Which job is running over HTTP, and when it last started (SEC-09).
--
-- GET /api/v1/jobs/<name> claims the job's row before it runs the job and releases it afterwards, so a
-- job runs once at a time however many instances receive the call, and no sooner than
-- JOB_MIN_SPACING_SECONDS after the previous start. Claim and release are single statements, each in
-- its own short transaction: nothing is held while the job runs, which is why this works through a
-- transaction pooler (a session-level advisory lock would not). lease_until is when a run that never
-- reported its end (a crash or a platform timeout) is presumed gone; a finished run sets it back to its
-- end. The command line (python -m app.cli job) does not use this table.
--
-- One row per job name. Only the worker role reads or writes it.

CREATE TABLE app.job_runs (
  job_name    text PRIMARY KEY,
  started_at  timestamptz NOT NULL,
  lease_until timestamptz NOT NULL
);

ALTER TABLE app.job_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.job_runs FORCE ROW LEVEL SECURITY;

GRANT SELECT, INSERT, UPDATE ON app.job_runs TO app_worker;
CREATE POLICY job_runs_worker ON app.job_runs FOR ALL TO app_worker USING (true) WITH CHECK (true);
