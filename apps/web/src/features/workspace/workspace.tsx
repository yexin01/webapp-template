"use client";

import { useAuth, useOrganization } from "@clerk/nextjs";
import { useCallback } from "react";

import { PlanBadge } from "@/features/billing";
import { LlmCheck } from "@/features/llm";
import { ProjectsPanel } from "@/features/projects";
import { useApi } from "@/lib/api/use-api";
import { useResource } from "@/lib/use-resource";
import { OperatorOnly } from "@/ui/operator-only";
import { ErrorState, LoadingState } from "@/ui/state-views";

/**
 * The active tenant follows Clerk's organization switcher. The role shown
 * here only adjusts presentation; the API re-verifies membership and role on
 * every request (FE-05). Panels remount per user and tenant, so nothing the
 * previous user or tenant loaded or created stays on screen after a switch.
 */
export function Workspace() {
  const { userId } = useAuth();
  const { isLoaded, organization, membership } = useOrganization();
  const api = useApi();
  const orgId = organization?.id ?? null;
  const fetchEntitlement = useCallback(() => api.entitlement(orgId), [api, orgId]);
  const entitlement = useResource(fetchEntitlement, `entitlement:${orgId ?? "personal"}`);
  // A failed entitlement lookup is shown as Free; the server still decides access.
  const plan = entitlement.loading ? null : (entitlement.data?.plan ?? "free");

  if (!isLoaded) {
    // Until Clerk loads, the only resource error is that it never did.
    return entitlement.error === undefined ? (
      <LoadingState label="Loading workspace…" />
    ) : (
      <ErrorState error={entitlement.error} onRetry={entitlement.reload} />
    );
  }

  const canDelete = orgId === null || membership?.role === "org:admin";

  return (
    <div className="stack">
      <div className="page-heading">
        <h1>{organization ? organization.name : "Personal workspace"}</h1>
        {plan && <PlanBadge plan={plan} />}
      </div>
      <ProjectsPanel key={`${userId}:${orgId ?? "personal"}`} orgId={orgId} canDelete={canDelete} />
      <OperatorOnly>
        <LlmCheck />
      </OperatorOnly>
    </div>
  );
}
