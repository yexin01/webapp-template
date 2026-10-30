"use client";

import { useAuth } from "@clerk/nextjs";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import { useApi } from "@/lib/api/use-api";

const DiagnosticsAccess = createContext(false);

/**
 * Whether the signed-in user may use the operator tools (the Integrations page
 * and the LLM check), as the API answers it (GET /diagnostics/access: the policy
 * of the diagnostics routes, without running anything or leaving a trace).
 * Anything but an affirmative answer, and the time before the answer, counts as
 * no access, so the tools show only to those who can use them. The API still
 * decides on every request (FE-05). One request per page load, shared by every
 * consumer; a customer's answer is a 200, so nothing fails in their console.
 */
export function DiagnosticsAccessProvider({ children }: { children: ReactNode }) {
  const { userId } = useAuth();
  const api = useApi();
  // The user the API granted access to; another user starts without it.
  const [grantedTo, setGrantedTo] = useState<string | null>(null);

  useEffect(() => {
    if (!userId) return;
    let cancelled = false;
    api.diagnosticsAccess().then(
      (access) => !cancelled && setGrantedTo(access.available ? userId : null),
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
