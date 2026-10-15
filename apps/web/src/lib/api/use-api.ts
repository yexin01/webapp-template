"use client";

import { useAuth } from "@clerk/nextjs";
import { useMemo } from "react";

import { createApiClient } from "./client";

/**
 * The API client for the signed-in user. Clerk's getToken is stable across
 * sessions, so the client is also keyed on the user: after an account switch
 * every callback and resource built on it is recreated and refetches.
 */
export function useApi() {
  const { getToken, userId } = useAuth();
  // eslint-disable-next-line react-hooks/exhaustive-deps -- userId: one client per signed-in user (see above)
  return useMemo(() => createApiClient(() => getToken()), [getToken, userId]);
}
