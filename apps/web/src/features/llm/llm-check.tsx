"use client";

import { useState } from "react";

import type { Schemas } from "@/lib/api/client";
import { useApi } from "@/lib/api/use-api";
import { ErrorState } from "@/ui/state-views";

export function LlmCheck() {
  const api = useApi();
  const [result, setResult] = useState<Schemas["LLMCheck"] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      setResult(await api.llmCheck());
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card" aria-labelledby="llm-title">
      <h2 id="llm-title">LLM connectivity</h2>
      <p className="muted">Verifies the configured provider and model without generating tokens.</p>
      <button type="button" className="button" onClick={() => void run()} disabled={busy}>
        {busy ? "Checking…" : "Run check"}
      </button>
      <div aria-live="polite">
        {result && (
          <p className={result.ok ? "notice notice-success" : "notice notice-warning"}>
            {result.ok
              ? `Connected to ${result.provider} (${result.model}) in ${result.latency_ms} ms.`
              : `Check failed for ${result.provider}: ${result.error_code ?? "unknown error"}.`}
          </p>
        )}
      </div>
      {error !== null && <ErrorState error={error} onRetry={() => void run()} />}
    </section>
  );
}
