"use client";

import { useAuth } from "@clerk/nextjs";
import { useCallback, useState } from "react";

import { ApiError } from "@/lib/api/errors";
import type { Schemas } from "@/lib/api/client";
import { useApi } from "@/lib/api/use-api";
import { useResource } from "@/lib/use-resource";
import { ErrorState, LoadingState } from "@/ui/state-views";

import { BrowserChecks } from "./browser-checks";
import { CheckOutcome, ModeBadge } from "./integration-status";

type Key = Schemas["IntegrationKey"];
type Run = { result?: Schemas["IntegrationCheck"]; error?: unknown };

/**
 * One page to see how every integration is configured and to exercise each
 * one with the keys in `.env`. All checks run on the server; the browser only
 * renders results. Mocked or disabled integrations are reported as skipped.
 * Remounts per signed-in user: check results can be about the user (their
 * Clerk memberships), so none survive an account switch.
 */
export function IntegrationsPanel() {
  const { userId } = useAuth();
  return <Integrations key={userId ?? "signed-out"} />;
}

function Integrations() {
  const api = useApi();
  const fetchReport = useCallback(() => api.integrations(), [api]);
  const { data: report, error, reload } = useResource(fetchReport, "integrations");
  const [runs, setRuns] = useState<Partial<Record<Key, Run>>>({});
  const [running, setRunning] = useState<Key | "all" | null>(null);

  async function runOne(key: Key): Promise<void> {
    try {
      const result = await api.runIntegrationCheck(key);
      setRuns((prev) => ({ ...prev, [key]: { result } }));
    } catch (e) {
      setRuns((prev) => ({ ...prev, [key]: { error: e } }));
    }
  }

  async function run(key: Key) {
    setRunning(key);
    await runOne(key);
    setRunning(null);
  }

  async function runAll(keys: Key[]) {
    setRunning("all");
    // Sequential, so a burst never trips the per-user rate limit mid-run.
    for (const key of keys) await runOne(key);
    setRunning(null);
  }

  // The API answers 404 to anyone who may not use diagnostics. The message
  // stays generic: internal configuration names are for operators' docs only.
  if (error instanceof ApiError && error.code === "NOT_FOUND") {
    return (
      <div className="stack">
        <h1>Integrations</h1>
        <p className="notice">Diagnostics are not available for your account in this environment.</p>
      </div>
    );
  }
  if (error) return <ErrorState error={error} onRetry={reload} />;
  if (!report) return <LoadingState label="Loading integrations…" />;

  const keys = report.integrations.map((i) => i.key);

  return (
    <div className="stack">
      <div className="page-heading">
        <h1>Integrations</h1>
        <span className="badge">Environment: {report.environment}</span>
      </div>
      <p className="muted">
        Each check calls the real service with the keys in <code>.env</code>. Mocked or disabled services are
        skipped, never reported as passing. A pass in sandbox mode proves connectivity with test credentials
        only.
      </p>
      <div className="button-row">
        <button
          type="button"
          className="button button-primary"
          disabled={running !== null}
          onClick={() => void runAll(keys)}
        >
          {running === "all" ? "Running checks…" : "Run all server checks"}
        </button>
      </div>

      <section aria-labelledby="server-checks-title">
        <h2 id="server-checks-title">Server checks</h2>
        <ul className="integration-grid">
          {report.integrations.map((integration) => {
            const current = runs[integration.key];
            const titleId = `integration-${integration.key}`;
            return (
              <li key={integration.key} className="card integration-card">
                <div className="integration-card-header">
                  <h3 id={titleId}>{integration.name}</h3>
                  <ModeBadge mode={integration.mode} />
                </div>
                <ul className="detail-list">
                  {integration.details.map((detail) => (
                    <li key={detail}>{detail}</li>
                  ))}
                </ul>
                <button
                  type="button"
                  className="button button-small"
                  disabled={running !== null}
                  aria-label={`${running === integration.key ? "Checking" : "Run check"} for ${integration.name}`}
                  onClick={() => void run(integration.key)}
                >
                  {running === integration.key ? "Checking…" : "Run check"}
                </button>
                <div aria-live="polite">
                  {current?.result && <CheckOutcome result={current.result} />}
                  {current?.error !== undefined && <ErrorState error={current.error} />}
                </div>
              </li>
            );
          })}
        </ul>
      </section>

      <BrowserChecks environment={report.environment} />
    </div>
  );
}
