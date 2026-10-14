# Pending owner actions

Changes that are prepared and tested but not applied, because applying them needs the owner.

## `web-import-boundaries.patch` (ADR-0003 §5)

Enforces the frontend feature boundaries with ESLint `import/no-restricted-paths`:

- `lib` may not import `ui`, `features`, or `app`;
- `ui` may not import `features` or `app`;
- a feature reaches another feature only through its `index.ts`.

The patch also adds 23 tests that lint real snippets through the real configuration.

It was not applied because a configuration-protection hook in the owner's environment blocks edits to `eslint.config.mjs`. When validated without editing that file (rule injected through ESLint's `overrideConfig`), all files lint clean and the 23 tests pass.

To apply it:

```bash
git apply dev-docs/pending/web-import-boundaries.patch
pnpm --filter web lint && pnpm --filter web test
git rm dev-docs/pending/web-import-boundaries.patch   # then update ADR-0003 §5 and the spec's traceability matrix
```
