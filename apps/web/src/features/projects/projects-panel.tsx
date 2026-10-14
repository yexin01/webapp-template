"use client";

import { useCallback, useId, useState, type FormEvent } from "react";

import type { Schemas } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import { useApi } from "@/lib/api/use-api";
import { useResource } from "@/lib/use-resource";
import { EmptyState, ErrorState, LoadingState } from "@/ui/state-views";

type Props = { orgId: string | null; canDelete: boolean };
type Project = Schemas["Project"];

const MAX_NAME = 120;

export function ProjectsPanel({ orgId, canDelete }: Props) {
  const api = useApi();
  const inputId = useId();
  const errorId = useId();
  const fetchFirstPage = useCallback(() => api.listProjects(orgId), [api, orgId]);
  const first = useResource(fetchFirstPage, `projects:${orgId ?? "personal"}`);
  const [added, setAdded] = useState<Project[]>([]);
  const [more, setMore] = useState<Project[]>([]);
  const [moreCursor, setMoreCursor] = useState<string | null | undefined>(undefined);
  const [removed, setRemoved] = useState<ReadonlySet<string>>(new Set());
  const [loadingMore, setLoadingMore] = useState(false);
  const [name, setName] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const items = [...added, ...(first.data?.items ?? []), ...more].filter((p) => !removed.has(p.id));
  const cursor = moreCursor === undefined ? (first.data?.next_cursor ?? null) : moreCursor;

  async function loadMore() {
    if (!cursor) return;
    setLoadingMore(true);
    try {
      const page = await api.listProjects(orgId, cursor);
      setMore((prev) => [...prev, ...page.items]);
      setMoreCursor(page.next_cursor);
    } catch (error) {
      setFormError(error instanceof ApiError ? error.message : "Could not load more projects.");
    } finally {
      setLoadingMore(false);
    }
  }

  function retry() {
    setAdded([]);
    setMore([]);
    setMoreCursor(undefined);
    setRemoved(new Set());
    first.reload();
  }

  async function onCreate(event: FormEvent) {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) {
      setFormError("Enter a project name.");
      return;
    }
    if (trimmed.length > MAX_NAME) {
      setFormError(`Use at most ${MAX_NAME} characters.`);
      return;
    }
    setBusy(true);
    setFormError(null);
    try {
      const created = await api.createProject(orgId, trimmed);
      setAdded((prev) => [created, ...prev]);
      setName("");
    } catch (error) {
      setFormError(error instanceof ApiError ? error.message : "Could not create the project.");
    } finally {
      setBusy(false);
    }
  }

  async function onDelete(project: Project) {
    if (!window.confirm(`Delete “${project.name}”? This cannot be undone.`)) return;
    try {
      await api.deleteProject(orgId, project.id);
      setRemoved((prev) => new Set(prev).add(project.id));
    } catch (error) {
      setFormError(error instanceof ApiError ? error.message : "Could not delete the project.");
    }
  }

  return (
    <section className="card" aria-labelledby="projects-title">
      <h2 id="projects-title">Projects</h2>
      <form className="inline-form" onSubmit={onCreate} noValidate>
        <label htmlFor={inputId}>New project name</label>
        <div className="inline-form-row">
          <input
            id={inputId}
            value={name}
            maxLength={MAX_NAME}
            onChange={(e) => setName(e.target.value)}
            aria-invalid={formError ? true : undefined}
            aria-describedby={formError ? errorId : undefined}
            autoComplete="off"
          />
          <button type="submit" className="button button-primary" disabled={busy}>
            {busy ? "Creating…" : "Create"}
          </button>
        </div>
        {formError && (
          <p id={errorId} className="field-error" role="alert">
            {formError}
          </p>
        )}
      </form>

      {first.loading && <LoadingState label="Loading projects…" />}
      {first.error !== undefined && <ErrorState error={first.error} onRetry={retry} />}
      {first.data && items.length === 0 && (
        <EmptyState title="No projects yet">
          <p>Create your first project above.</p>
        </EmptyState>
      )}
      {first.data && items.length > 0 && (
        <ul className="list">
          {items.map((project) => (
            <li key={project.id} className="list-item">
              <span>{project.name}</span>
              <span className="muted">{new Date(project.created_at).toLocaleDateString()}</span>
              {canDelete && (
                <button
                  type="button"
                  className="button button-small"
                  onClick={() => void onDelete(project)}
                  aria-label={`Delete ${project.name}`}
                >
                  Delete
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {first.data && cursor && (
        <button type="button" className="button" onClick={() => void loadMore()} disabled={loadingMore}>
          {loadingMore ? "Loading…" : "Load more"}
        </button>
      )}
    </section>
  );
}
