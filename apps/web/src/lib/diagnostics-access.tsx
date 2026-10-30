"use client";

import { useAuth } from "@clerk/nextjs";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import { useApi } from "@/lib/api/use-api";

const DiagnosticsAccess = createContext(false);

/**
 * Whether the signed-in user may use the operator tools (the Integrations page
 * and the LLM check), as the API answers it: the API lists integrations only
 * for an operator while diagnostics are enabled, and answers 404 otherwise.
 * Anything but a success, and the time before the answer, counts as no access,
 * so the tools show only to those who can use them. The API still decides on
 * every request (FE-05). One request per page load, shared by every consumer.
 */
export function DiagnosticsAccessProvider({ children }: { children: ReactNode }) {
  const { userId } = useAuth();
  const api = useApi();
  // The user the API granted access to; another user starts without it.
  const [grantedTo, setGrantedTo] = useState<string | null>(null);

  useEffect(() => {
    if (!userId) return;
    let cancelled = false;
    api.integrations().then(
      () => !cancelled && setGrantedTo(userId),
      () => !cancelled && setGrantedTo(null),
    );
    return () => {
      cancelled = true;
    };
  }, [api, userId]);

  return (
    <DiagnosticsAccess.Provider value={userId != null && grantedTo === userId}>
      {children}
    </DiagnosticsAccess.Provider>
  );
}

export function useDiagnosticsAccess(): boolean {
  return useContext(DiagnosticsAccess);
}
