import type { ReactNode } from "react";

import type { Schemas } from "@/lib/api/client";

type Mode = Schemas["IntegrationMode"];

const MODE_LABELS: Record<Mode, string> = {
  local: "Local",
  mock: "Mock",
  sandbox: "Sandbox",
  live: "Live",
  disabled: "Disabled",
};

const OUTCOME_LABELS: Record<Schemas["Outcome"], string> = {
  passed: "Passed",
  sent: "Sent (unconfirmed)",
  failed: "Failed",
  skipped: "Skipped",
};

export function ModeBadge({ mode }: { mode: Mode }) {
  return (
    <span className={`badge badge-mode badge-mode-${mode}`}>
      <span className="visually-hidden">Mode: </span>
      {MODE_LABELS[mode]}
    </span>
  );
}

export function OutcomeMessage({ outcome, children }: { outcome: Schemas["Outcome"]; children: ReactNode }) {
  return (
    <div className={`check-outcome check-outcome-${outcome}`}>
      <span className="check-outcome-label">{OUTCOME_LABELS[outcome]}</span>
      <div>{children}</div>
    </div>
  );
}

export function CheckOutcome({ result }: { result: Schemas["IntegrationCheck"] }) {
  return (
    <OutcomeMessage outcome={result.outcome}>
      <p>{result.summary}</p>
      <p className="muted check-meta">
        {result.outcome !== "skipped" && <>{result.latency_ms} ms</>}
        {result.error_code && <> · code {result.error_code}</>}
        {result.reference && (
          <>
            {" "}
            · reference <code>{result.reference}</code>
          </>
        )}
      </p>
    </OutcomeMessage>
  );
}
