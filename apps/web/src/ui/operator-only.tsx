"use client";

import type { ReactNode } from "react";

import { useDiagnosticsAccess } from "@/lib/diagnostics-access";

/** Operator tools (diagnostics): rendered only for a user the API lets use them. */
export function OperatorOnly({ children }: { children: ReactNode }) {
  return useDiagnosticsAccess() ? <>{children}</> : null;
}
