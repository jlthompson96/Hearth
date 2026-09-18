`schema.d.ts` is generated, not written. Never hand-edit it.

With the backend running:

    npm run gen:types

It reads http://localhost:8000/openapi.json and writes the TypeScript types the
UI uses for every response. Regenerate it whenever a response model changes.

It is committed rather than gitignored, for two reasons: a fresh clone must
typecheck without a backend running, and a change to the API's shape should be
visible in a diff rather than appearing silently on someone's next build.
