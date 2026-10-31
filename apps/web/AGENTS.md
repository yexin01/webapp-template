<!-- BEGIN:nextjs-agent-rules -->

## This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

## Rendering baseline

Route files in `src/app` are Server Components that render static content and mount feature client components. API data loads in the browser (`useApi`, `useResource`). In `src/app` there is no Route Handler (`route.ts`), no Server Action (`"use server"`), and no server-side Clerk (`auth()`, anything from `@clerk/nextjs/server`). The proxy, `src/proxy.ts`, is outside `src/app` and keeps using Clerk's server API.

`src/rendering-baseline.test.ts` fails when `src/app` breaks this. Fetching on the server is a deliberate change: it needs an ADR and an edit to that test in the same change ([ADR-0004 §6](../../dev-docs/adr/0004-cycle-1-adopted-decisions.md), [docs/architecture.md](../../docs/architecture.md#frontend)).
