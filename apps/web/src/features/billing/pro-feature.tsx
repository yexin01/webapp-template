"use client";

import { useOrganization } from "@clerk/nextjs";
import { useCallback } from "react";

import { ApiError } from "@/lib/api/errors";
import { useApi } from "@/lib/api/use-api";
import { useResource } from "@/lib/use-resource";
import { ErrorState, LoadingState } from "@/ui/state-views";

import { Paywall } from "./paywall";

/** Example Pro-gated feature. Access is decided by the server only. */
export function ProFeature() {
  const api = useApi();
  const { organization, isLoaded } = useOrganization();
  const orgId = organization?.id ?? null;
  const fetchInsight = useCallback(() => api.proInsight(orgId), [api, orgId]);
  const { data, error, reload } = useResource(fetchInsight, `pro:${orgId ?? "personal"}`);

  if (!isLoaded) return <LoadingState />;

  if (error instanceof ApiError && error.code === "ENTITLEMENT_REQUIRED") {
    return <Paywall feature="pro_insight" inOrganization={orgId !== null} />;
  }
  if (error) return <ErrorState error={error} onRetry={reload} />;
  if (!data) return <LoadingState />;
  return (
    <section className="card" aria-labelledby="pro-title">
      <h1 id="pro-title">Pro insight</h1>
      <p>{data.message}</p>
      <p className="muted">Generated {new Date(data.generated_at).toLocaleString()}</p>
    </section>
  );
}
