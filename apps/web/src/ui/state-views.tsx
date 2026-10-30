"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { ApiError, presentError } from "@/lib/api/errors";

export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="state" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="state state-empty">
      <p className="state-title">{title}</p>
      {children}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const pathname = usePathname();
  const view = presentError(error);
  return (
    <div className="state state-error" role="alert">
      <p className="state-title">{view.title}</p>
      <p>{view.message}</p>
      {error instanceof ApiError && error.status >= 500 && error.requestId && (
        // The API's X-Request-ID: what support asks for, and what its logs and error reports carry (OBS-05).
        <p className="muted">Reference: {error.requestId}</p>
      )}
      {view.action === "sign-in" && (
        <Link className="button" href={`/sign-in?redirect_url=${encodeURIComponent(pathname)}`}>
          Sign in
        </Link>
      )}
      {view.action === "upgrade" && (
        <Link className="button" href="/billing">
          See plans
        </Link>
      )}
      {view.action === "retry" && onRetry && (
        <button type="button" className="button" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}

export function SuccessMessage({ children }: { children: ReactNode }) {
  return (
    <p className="notice notice-success" role="status">
      {children}
    </p>
  );
}
