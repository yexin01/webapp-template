"use client";

import { useOrganization } from "@clerk/nextjs";

/**
 * The tenant API calls are made in: Clerk's active organization, or null for the personal workspace.
 * orgId is null until isLoaded, so fetch only once it is true. It names the tenant and nothing else:
 * what the user may do there comes from the API's responses, never from Clerk's role (ARCH-03).
 */
export function useActiveTenant(): { isLoaded: boolean; orgId: string | null } {
  const { isLoaded, organization } = useOrganization();
  return { isLoaded, orgId: organization?.id ?? null };
}
