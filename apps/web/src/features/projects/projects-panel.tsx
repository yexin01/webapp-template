"use client";

import { useCallback, useId, useRef, useState, type FormEvent } from "react";

import type { Schemas } from "@/lib/api/client";
import { ApiError, isInputError, presentError } from "@/lib/api/errors";
import { useApi } from "@/lib/api/use-api";
import { useResource } from "@/lib/use-resource";
import { EmptyState, ErrorState, LoadingState } from "@/ui/state-views";
import { focusIn, useFocusAfterRender } from "@/ui/use-focus-after-render";

type Props = { orgId: string | null; canDelete: boolean };
type Project = Schemas["Project"];
/** A failed action, shown through the shared error view (FE-10) rather than on the name field. */
type ActionError = { error: unknown; retry?: () => void };

const MAX_NAME = 120;

export function ProjectsPanel({ orgId, canDelete }: Props) {
  const api = useApi();
  const inputId = useId();
  const errorId = useId();
  const rowId = useId();
  const fetchFirstPage = useCallback(() => api.listProjects(orgId), [api, orgId]);
  const first = useResource(fetchFirstPage, `projects:${orgId ?? "personal"}`);
  const [added, setAdded] = useState<Project[]>([]);
  const [more, setMore] = useState<Project[]>([]);
  const [moreCursor, setMoreCursor] = useState<string | null | undefined>(undefined);
  const [removed, setRemoved] = useState<ReadonlySet<string>>(new Set());
  const [deleting, setDeleting] = useState<ReadonlySet<string>>(new Set());
  const [loadingMore, setLoadingMore] = useState(false);
  const [name, setName] = useState("");
  // Only problems with the entered name; they mark the field invalid.
  const [formError, setFormError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<ActionError | null>(null);
  const [busy, setBusy] = useState(false);
  // Results for screen readers (FE-09), in a live region that stays mounted so each one is read.
  const [announcement, setAnnouncement] = useState("");
  const headingRef = useRef<HTMLHeadingElement>(null);
  const loadMoreRef = useRef<HTMLButtonElement>(null);
  const actionErrorRef = useRef<HTMLDivElement>(null);
  const deleteButtons = useRef(new Map<string, HTMLButtonElement>());
  const focusAfterRender = useFocusAfterRender();

  // A project created while the first page loads can be on that page too: list it once.
  const listed = new Set<string>();
  const items = [...added, ...(first.data?.items ?? []), ...more].filter((p) => {
    const show = !removed.has(p.id) && !listed.has(p.id);
    listed.add(p.id);
    return show;
  });
  const cursor = moreCursor === undefined ? (first.data?.next_cursor ?? null) : moreCursor;

  async function loadMore() {
    if (!cursor) return;
    // Load more is disabled while loading, and Try again goes away; either may hold focus.
    const origin = focusIn(loadMoreRef.current, actionErrorRef.current);
    setLoadingMore(true);
    setActionError(null);
    try {
      const page = await api.listProjects(orgId, cursor);
      setMore((prev) => [...prev, ...page.items]);
      setMoreCursor(page.next_cursor);
      const firstNew = page.items[0];
      if (origin) {
        focusAfterRender(
          () =>
            page.next_cursor
              ? loadMoreRef.current
              : (firstNew && deleteButtons.current.get(firstNew.id)) || headingRef.current,
          origin,
        );
      }
    } catch (error) {
      // A cursor the server refuses fails the same way when sent again: Load more goes, and
      // Try again starts over from the first page.
      const cursorRefused = error instanceof ApiError && error.code === "INVALID_REQUEST";
      if (cursorRefused) setMoreCursor(null);
      setActionError({ error, retry: cursorRefused ? retry : () => void loadMore() });
      if (origin) {
        focusAfterRender(
          () =>
            cursorRefused
              ? (actionErrorRef.current?.querySelector("button") ?? headingRef.current)
              : loadMoreRef.current,
          origin,
        );
      }
    } finally {
      setLoadingMore(false);
    }
  }

  function retry() {
    // Try again goes away with the error it belongs to.
    headingRef.current?.focus();
    setAdded([]);
    setMore([]);
    setMoreCursor(undefined);
    setRemoved(new Set());
    setActionError(null);
    first.reload();
  }

  async function onCreate(event: FormEvent) {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) {
      setFormError("Enter a project name.");
      return;
    }
    // Characters as the server counts them (code points): an emoji is one, not two.
    if ([...trimmed].length > MAX_NAME) {
      setFormError(`Use at most ${MAX_NAME} characters.`);
      return;
    }
    setBusy(true);
    setFormError(null);
    setActionError(null);
    setAnnouncement("");
    try {
      const created = await api.createProject(orgId, trimmed);
      setAdded((prev) => [created, ...prev]);
      setName("");
      setAnnouncement(`Created “${created.name}”.`);
    } catch (error) {
      // The name stays in the field, so nothing is lost if the session expired (FE-04).
      if (isInputError(error)) setFormError(presentError(error).message);
      else setActionError({ error });
    } finally {
      setBusy(false);
    }
  }

  /** Takes a deleted project off the list, moving focus to its neighbor if its button had it. */
  function removeProject(project: Project, origin: Element | null) {
    const index = items.findIndex((p) => p.id === project.id);
    const neighbor = index === -1 ? undefined : (items[index + 1] ?? items[index - 1]);
    setRemoved((prev) => new Set(prev).add(project.id));
    setAnnouncement(`Deleted “${project.name}”.`);
    if (origin) {
      focusAfterRender(
        () => (neighbor && deleteButtons.current.get(neighbor.id)) || headingRef.current,
        origin,
      );
    }
  }

  async function onDelete(project: Project) {
    if (deleting.has(project.id)) return;
    if (!window.confirm(`Delete “${project.name}”? This cannot be undone.`)) return;
    // The button is disabled while the request runs, then removed.
    const origin = focusIn(deleteButtons.current.get(project.id));
    setDeleting((prev) => new Set(prev).add(project.id));
    setActionError(null);
    setAnnouncement("");
    try {
      await api.deleteProject(orgId, project.id);
      removeProject(project, origin);
    } catch (error) {
      // Already gone (another tab or member deleted it): what the user asked for is done.
      if (error instanceof ApiError && error.code === "NOT_FOUND") {
        removeProject(project, origin);
      } else {
        setActionError({ error });
        if (origin) focusAfterRender(() => deleteButtons.current.get(project.id), origin);
      }
    } finally {
      setDeleting((prev) => {
        const next = new Set(prev);
        next.delete(project.id);
        return next;
      });
    }
  }

  return (
    <section className="card" aria-labelledby="projects-title">
      <h2 id="projects-title" ref={headingRef} tabIndex={-1}>
        Projects
      </h2>
      <form className="inline-form" onSubmit={onCreate} noValidate>
        <label htmlFor={inputId}>New project name</label>
        <div className="inline-form-row">
          <input
            id={inputId}
            value={name}
            // In UTF-16 units: room for MAX_NAME characters of any kind; onCreate checks the count.
            maxLength={MAX_NAME * 2}
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
      <p className="visually-hidden" role="status" aria-live="polite">
        {announcement}
      </p>
      {actionError && (
        <div ref={actionErrorRef}>
          <ErrorState error={actionError.error} onRetry={actionError.retry} />
        </div>
      )}

      {first.loading && <LoadingState label="Loading projects…" />}
      {first.error !== undefined && <ErrorState error={first.error} onRetry={retry} />}
      {first.data && items.length === 0 && (
        <EmptyState title="No projects yet">
          <p>Create your first project above.</p>
        </EmptyState>
      )}
      {first.data && items.length > 0 && (
        <ul className="list">
          {items.map((project, index) => (
            <li key={project.id} className="list-item">
              <span id={`${rowId}-name-${index}`}>{project.name}</span>
              <span className="muted">{new Date(project.created_at).toLocaleDateString()}</span>
              {canDelete && (
                // Named "Delete <name>" through aria-labelledby: error reports copy aria-label, never text.
                <button
                  ref={(button) => {
                    if (!button) return;
                    deleteButtons.current.set(project.id, button);
                    return () => {
                      deleteButtons.current.delete(project.id);
                    };
                  }}
                  id={`${rowId}-delete-${index}`}
                  type="button"
                  className="button button-small"
                  aria-labelledby={`${rowId}-delete-${index} ${rowId}-name-${index}`}
                  disabled={deleting.has(project.id)}
                  onClick={() => void onDelete(project)}
                >
                  {deleting.has(project.id) ? "Deleting…" : "Delete"}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {first.data && cursor && (
        <button
          ref={loadMoreRef}
          type="button"
          className="button"
          onClick={() => void loadMore()}
          disabled={loadingMore}
        >
          {loadingMore ? "Loading…" : "Load more"}
        </button>
      )}
    </section>
  );
}
