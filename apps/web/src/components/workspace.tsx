"use client";

import { useOrganization } from "@clerk/nextjs";
import { useCallback } from "react";

import { useApi } from "@/lib/api/use-api";
import { useResource } from "@/lib/use-resource";

import { LlmCheck } from "./llm-check";
import { PlanBadge } from "./plan-badge";
import { ProjectsPanel } from "./projects-panel";
import { LoadingState } from "./state-views";

/**
 * The active tenant follows Clerk's organization switcher. The role shown
 * here only adjusts presentation; the API re-verifies membership and role on
 * every request (FE-05).
 */
export function Workspace() {
  const { isLoaded, organization, membership } = useOrganization();
  const api = useApi();
  const orgId = organization?.id ?? null;
  const fetchEntitlement = useCallback(() => api.entitlement(orgId), [api, orgId]);
  const entitlement = useResource(fetchEntitlement, `entitlement:${orgId ?? "personal"}`);
  // A failed entitlement lookup is shown as Free; the server still decides access.
  const plan = entitlement.loading ? null : (entitlement.data?.plan ?? "free");

  if (!isLoaded) return <LoadingState label="Loading workspace…" />;

  const canDelete = orgId === null || membership?.role === "org:admin";

  return (
    <div className="stack">
      <div className="page-heading">
        <h1>{organization ? organization.name : "Personal workspace"}</h1>
        {plan && <PlanBadge plan={plan} />}
      </div>
      {organization && plan === "free" && (
        <p className="muted">Pro features are not yet available inside organizations.</p>
      )}
      <ProjectsPanel key={orgId ?? "personal"} orgId={orgId} canDelete={canDelete} />
      <LlmCheck />
    </div>
  );
}
