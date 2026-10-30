"use client";

import { useOrganization } from "@clerk/nextjs";
import { useCallback } from "react";

import { PlanBadge } from "@/features/billing";
import { LlmCheck } from "@/features/llm";
import { ProjectsPanel } from "@/features/projects";
import { useApi } from "@/lib/api/use-api";
import { useActiveTenant } from "@/lib/use-active-tenant";
import { useResource } from "@/lib/use-resource";
import { OperatorOnly } from "@/ui/operator-only";
import { ErrorState, LoadingState } from "@/ui/state-views";

/**
 * The active tenant follows Clerk's organization switcher. What the user may
 * do in it is decided by the API and shown by the panels (ARCH-03, FE-05).
 */
export function Workspace() {
  const { isLoaded, orgId } = useActiveTenant();
  const { organization } = useOrganization(); // for the heading only
  const api = useApi();
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

  return (
    <div className="stack">
      <div className="page-heading">
        <h1>{organization ? organization.name : "Personal workspace"}</h1>
        {plan && <PlanBadge plan={plan} />}
      </div>
      <ProjectsPanel />
      <OperatorOnly>
        <LlmCheck />
      </OperatorOnly>
    </div>
  );
}
